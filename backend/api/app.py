"""
Sovereign Workbench - local web UI.

Requirement:
  "...show, through logs or a visible network monitor, that no external
   calls are made at any point."

So the sovereignty panel is not a sticker. It reads live from
core.airgap.MONITOR - the same object that intercepts every socket call.

Stack choice (deliberate):
  - NOT Streamlit/Gradio: both send telemetry by default. During a
    sovereignty demo our own network log would catch the leak.
  - NOT Next.js: node + npm install + a build step + CDN fonts. All three
    are problems on a sealed machine.
  - FastAPI + plain HTML/CSS/JS: already in the venv, no npm, no CDN, no
    build. SSE is native to the browser, so streaming needs no library.

Run:
    python -m api.app           # http://127.0.0.1:8000  (from backend/)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import AsyncIterator

from core import airgap

MONITOR = airgap.seal()          # <- before anything else. Everything is sealed now.

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.llm import Client
from ingest import pipeline
from api import chats
from agents.workbench import Agent
from tools import deliverables as deliv

_HERE = Path(__file__).resolve().parent
# The frontend is a sibling of backend/, deployed separately.
_STATIC = _HERE.parent.parent / "frontend"

app = FastAPI(title="Sovereign Workbench")
CLIENT = Client()
AGENT = Agent(CLIENT)

# Files uploaded in this session, newest first. When someone asks "what is in
# this file" right after dropping one in, retrieval must look THERE rather
# than run a vague search across the whole corpus and come back empty.
RECENT_UPLOADS: list[str] = []
RECENT_MAX = 8

# Drawings are tracked separately. A P&ID yields zero chunks - its content is
# geometry, not text - so it never reaches the index and the ordinary
# "recent uploads" path cannot help. The agent needs the FILE.
RECENT_DRAWINGS: list[str] = []
DRAWING_EXT = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp", ".pdf"}

# Every uploaded image, whether or not OCR found text in it. A handwritten note
# DOES index (OCR recovers most of it), so it never lands in RECENT_DRAWINGS -
# but the vision lane still reads it better than OCR does, so the file has to
# be kept either way.
RECENT_IMAGES: list[str] = []
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}


def _remember_image(path: Path) -> None:
    p = str(path)
    if p in RECENT_IMAGES:
        RECENT_IMAGES.remove(p)
    RECENT_IMAGES.insert(0, p)
    del RECENT_IMAGES[4:]


def _remember_drawing(path: Path) -> None:
    p = str(path)
    if p in RECENT_DRAWINGS:
        RECENT_DRAWINGS.remove(p)
    RECENT_DRAWINGS.insert(0, p)
    del RECENT_DRAWINGS[4:]


def _remember_upload(name: str) -> None:
    if name in RECENT_UPLOADS:
        RECENT_UPLOADS.remove(name)
    RECENT_UPLOADS.insert(0, name)
    del RECENT_UPLOADS[RECENT_MAX:]

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# boot / status
# ---------------------------------------------------------------------------


@app.get("/api/boot")
def boot() -> dict:
    """On page load: what is running, what is indexed, how the seal looks."""
    health = CLIENT.health()
    try:
        idx = pipeline.status()
    except Exception as exc:
        idx = {"indexed": False, "chunks": 0, "sources": [], "error": str(exc)}
    return {
        "health": health,
        "index": idx,
        "sovereignty": MONITOR.summary(),
        "corpus_dir": str(pipeline.CORPUS_DIR),
    }


@app.get("/api/sovereignty")
def sovereignty() -> dict:
    """Live panel - polled every couple of seconds."""
    recent = [
        {"host": a.host, "port": a.port, "verdict": a.verdict,
         "blocked": a.blocked, "hint": a.stack_hint, "ts": a.ts}
        for a in MONITOR.attempts[-40:]
    ]
    return {**MONITOR.summary(), "recent": recent, "total": len(MONITOR.attempts)}


# ---------------------------------------------------------------------------
# ask — poora agent loop, SSE pe
# ---------------------------------------------------------------------------


async def _ask_stream(q: str, k: int, chat_id: str | None = None) -> AsyncIterator[str]:
    """
    Bridge the agent's event generator onto SSE.

    The agent is a blocking generator (it calls local models), so it runs in a
    worker thread and pushes events onto an asyncio queue. Everything the
    agent yields is forwarded verbatim - the UI renders the activity feed from
    these events, so the agent stays the single source of truth about what
    happened.
    """
    loop = asyncio.get_running_loop()

    past = chats.history(chat_id) if chat_id else []
    if chat_id:
        chats.append(chat_id, "user", q)

    queue: asyncio.Queue = asyncio.Queue()
    state: dict = {"answer": "", "sources": [], "done": None}

    def produce() -> None:
        try:
            for ev in AGENT.run(q, history=past, k=k,
                                recent_files=list(RECENT_UPLOADS),
                                drawing=RECENT_DRAWINGS[0] if RECENT_DRAWINGS else None,
                                image=RECENT_IMAGES[0] if RECENT_IMAGES else None):
                if ev["type"] == "token":
                    state["answer"] += ev["text"]
                elif ev["type"] == "sources":
                    state["sources"] = ev["items"]
                elif ev["type"] == "done":
                    state["done"] = ev
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "error", "message": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, produce)

    while True:
        ev = await queue.get()
        if ev is None:
            break
        etype = ev.pop("type")
        if etype == "done":
            ev["sovereignty"] = MONITOR.summary()
        yield _sse(etype, ev)

    d = state["done"]
    if chat_id and d and state["answer"]:
        chats.append(chat_id, "assistant", state["answer"], {
            "model": d.get("model"), "lane": d.get("lane"),
            "grounded": d.get("grounded"), "files": d.get("files") or [],
            "sources": state["sources"],
        })


@app.get("/api/ask")
async def ask(q: str, k: int = 0, chat_id: str = "") -> StreamingResponse:
    k = k or int(CLIENT.reg.retrieval.get("k", 4))
    return StreamingResponse(
        _ask_stream(q, k, chat_id or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# chats
# ---------------------------------------------------------------------------


@app.get("/api/chats")
def api_chats() -> dict:
    return {"chats": chats.list_chats()}


@app.post("/api/chats")
def api_chat_new() -> dict:
    return {"id": chats.create()}


@app.get("/api/chats/{cid}")
def api_chat_get(cid: str) -> dict:
    c = chats.get(cid)
    return c or {"messages": [], "title": "New chat"}


@app.delete("/api/chats/{cid}")
def api_chat_del(cid: str) -> dict:
    chats.delete(cid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# corpus management
# ---------------------------------------------------------------------------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    """Drag-drop -> data/corpus -> indexed immediately. Nothing leaves the box."""
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in pipeline.SUPPORTED:
        return JSONResponse(
            {"ok": False, "error": f"{Path(name).suffix} is not supported"},
            status_code=400,
        )
    dest = pipeline.CORPUS_DIR / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(
        None, lambda: pipeline.build([dest], client=CLIENT, verbose=False)
    )
    indexed = stats.get("chunks", 0) > 0
    if dest.suffix.lower() in IMAGE_EXT:
        _remember_image(dest)
    if indexed:
        _remember_upload(name)
    elif dest.suffix.lower() in DRAWING_EXT:
        # Zero chunks from an image is the signature of a drawing, so keep the
        # path for analyze_pid instead of treating it as a failed upload.
        _remember_drawing(dest)
    return JSONResponse({"ok": True, "file": name, "stats": stats,
                         "indexed": indexed,
                         "note": None if indexed else
                                 "No text extracted - treating this as a "
                                 "drawing. Ask about its equipment, tags or "
                                 "isolation and it will be analysed.",
                         "index": pipeline.status()})


@app.post("/api/reindex")
async def reindex(rebuild: bool = False) -> dict:
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(
        None, lambda: pipeline.build(rebuild=rebuild, client=CLIENT, verbose=False)
    )
    return {"stats": stats, "index": pipeline.status()}


# ---------------------------------------------------------------------------
# deliverables
# ---------------------------------------------------------------------------


@app.get("/api/deliverables")
def api_deliverables() -> dict:
    return {"files": deliv.list_deliverables()}


@app.get("/api/deliverables/{name}")
def api_deliverable_get(name: str):
    """Serve a generated file. Path is pinned to the output dir on purpose."""
    from fastapi.responses import FileResponse
    safe = Path(name).name
    path = deliv.OUT_DIR / safe
    if not path.exists() or path.parent != deliv.OUT_DIR:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, filename=safe,
                        media_type="application/octet-stream")


# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------


def _asset_version() -> str:
    """
    Stamp every asset URL with the newest mtime across the frontend.

    Without this the browser reuses a cached app.css / main.js forever: a fix
    ships, the page never sees it, and the bug looks unfixed. Ten lines here
    beats telling everyone to hard-reload.
    """
    newest = 0.0
    for f in _STATIC.iterdir():
        if f.is_file():
            newest = max(newest, f.stat().st_mtime)
    return hashlib.md5(str(newest).encode()).hexdigest()[:8]


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (_STATIC / "index.html").read_text()
    v = _asset_version()
    html = html.replace("/static/app.css", f"/static/app.css?v={v}")
    html = html.replace("/static/main.js", f"/static/main.js?v={v}")
    # index.html itself must never be cached, or the stamped URLs never arrive
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


def main() -> None:
    import uvicorn
    pipeline._quiet()
    print("Sovereign Workbench  ->  http://127.0.0.1:8000")
    print(f"corpus: {pipeline.CORPUS_DIR}")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
