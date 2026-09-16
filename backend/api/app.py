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

# ---------------------------------------------------------------------------
# working context, PER CHAT
# ---------------------------------------------------------------------------
# What is currently being worked on: which files were uploaded, which drawing
# is on the table, the brief describing each one, which folder was picked.
#
# This used to be five module-level lists - one set for the whole server. Open
# a new chat and the assistant still answered about the previous chat's file,
# because the process only ever had one "current file". A chat that looks clean
# must be clean underneath. So every entry below is keyed by chat id, and the
# same keying is what lets a chat you come back to still know its own context.
#
# Why each list exists:
#   uploads  - "what is in this file" right after a drop must look THERE, not
#              run a vague search over the whole corpus and come back empty
#   drawings - a P&ID yields zero chunks (its content is geometry, not text) so
#              it never reaches the index; the agent needs the FILE
#   images   - a handwritten note DOES index, so it is not a "drawing", but the
#              vision lane still reads it better than OCR does
#   briefs   - what each upload actually IS, worked out once at upload time;
#              without it the agent held a path and no idea what was in it
RECENT_MAX = 8
BRIEFS_MAX = 5
DRAWING_EXT = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp", ".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

# chat_id -> working context. "" is the scratch context used by callers that
# genuinely have no chat (the CLI benchmarks, a bare curl); it is never shown
# to a real chat.
SESSIONS: dict[str, dict] = {}


def sess(chat_id: str | None) -> dict:
    return SESSIONS.setdefault(chat_id or "", {
        "uploads": [], "drawings": [], "images": [], "briefs": [],
        "folder": None,
    })


try:
    from api import memory as _mem
    for _cid, _v in _mem.load().items():
        SESSIONS[_cid] = {**_v, "briefs": _mem.restore_briefs(_v.get("briefs", []))}
except Exception:
    pass


def _remember() -> None:
    try:
        from api import memory as _m
        _m.save(SESSIONS)
    except Exception:
        pass


def _push(seq: list, value, cap: int) -> None:
    if value in seq:
        seq.remove(value)
    seq.insert(0, value)
    del seq[cap:]


def _remember_image(chat_id: str | None, path: Path) -> None:
    _push(sess(chat_id)["images"], str(path), 4)
    _remember()


def _remember_drawing(chat_id: str | None, path: Path) -> None:
    _push(sess(chat_id)["drawings"], str(path), 4)
    _remember()


def _remember_upload(chat_id: str | None, name: str) -> None:
    _push(sess(chat_id)["uploads"], name, RECENT_MAX)
    _remember()


def _remember_brief(chat_id: str | None, b) -> None:
    briefs = sess(chat_id)["briefs"]
    briefs[:] = [x for x in briefs if x.name != b.name]
    briefs.insert(0, b)
    del briefs[BRIEFS_MAX:]
    _remember()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# boot / status
# ---------------------------------------------------------------------------


@app.get("/api/boot")
def boot(chat_id: str = "") -> dict:
    """On page load: what is running, what is indexed, how the seal looks.

    `remembered` is this CHAT's context. A chat id that has never uploaded
    anything gets an empty list - which is the whole point: a new chat used to
    boot showing the previous chat's file.
    """
    health = CLIENT.health()
    try:
        idx = pipeline.status()
    except Exception as exc:
        idx = {"indexed": False, "chunks": 0, "sources": [], "error": str(exc)}
    return {
        "remembered": [b.line() for b in sess(chat_id)["briefs"][:3]],
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
            ctx = sess(chat_id)
            for ev in AGENT.run(q, history=past, k=k,
                                recent_files=list(ctx["uploads"]),
                                drawing=ctx["drawings"][0] if ctx["drawings"] else None,
                                image=ctx["images"][0] if ctx["images"] else None,
                                briefs=list(ctx["briefs"]),
                                folder=ctx["folder"]):
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
async def upload(file: UploadFile = File(...),
                 chat_id: str = "") -> JSONResponse:
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
    chunks = stats.get("chunks", 0)
    try:
        from tools.brief import describe
        b = await loop.run_in_executor(None, lambda: describe(dest, chunks))
        _remember_brief(chat_id, b)
    except Exception:
        b = None

    indexed = stats.get("chunks", 0) > 0
    if dest.suffix.lower() in IMAGE_EXT:
        _remember_image(chat_id, dest)
    if indexed:
        _remember_upload(chat_id, name)
    elif dest.suffix.lower() in DRAWING_EXT:
        # Zero chunks from an image is the signature of a drawing, so keep the
        # path for analyze_pid instead of treating it as a failed upload.
        _remember_drawing(chat_id, dest)
    return JSONResponse({"ok": True, "file": name, "stats": stats,
                         "indexed": indexed,
                         "brief": b.as_dict() if b else None,
                         "summary": b.line() if b else None,
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
# folder connector
# ---------------------------------------------------------------------------


@app.get("/api/folder/places")
def api_folder_places() -> dict:
    from tools import folder
    return {"places": folder.places()}


@app.get("/api/folder/list")
def api_folder_list(path: str = "") -> dict:
    """Browse the local filesystem. The backend runs here, so it can."""
    from tools import folder
    return folder.listdir(path or str(Path.home()))


@app.post("/api/folder/choose")
def api_folder_choose() -> dict:
    """Ask the OS for a folder. Local process, so it can."""
    from tools import folder
    return folder.choose()


# The folder the user last chose, per chat. "Analyse my folder" should not
# require retyping a path that was just picked from a dialog - but the folder
# picked in one chat is not the folder a different chat is working on.
@app.post("/api/folder/select")
def api_folder_select(path: str, chat_id: str = "") -> dict:
    p = Path(path).expanduser()
    if not p.is_dir():
        return {"ok": False, "error": f"Not a folder: {p}"}
    sess(chat_id)["folder"] = str(p)
    _remember()
    return {"ok": True, "path": str(p)}


async def _folder_stream(path: str) -> AsyncIterator[str]:
    """Bridge the folder agent onto SSE, same shape as /api/ask."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def produce() -> None:
        try:
            from agents.folder_agent import analyse
            for ev in analyse(path, client=CLIENT):
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


@app.get("/api/folder/analyse")
async def api_folder_analyse(path: str, chat_id: str = "") -> StreamingResponse:
    """Chosen a folder? Then say what is in it, without being asked."""
    p = Path(path).expanduser()
    if p.is_dir():
        sess(chat_id)["folder"] = str(p)
        _remember()
    return StreamingResponse(
        _folder_stream(str(p)), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/folder/preview")
def api_folder_preview(path: str) -> dict:
    """What is in that folder, without reading anything."""
    from tools import folder
    return folder.preview(path).as_dict()


@app.post("/api/folder/ingest")
async def api_folder_ingest(path: str, chat_id: str = "") -> dict:
    """Index a local folder in place. Nothing is copied, nothing leaves."""
    from tools import folder
    loop = asyncio.get_running_loop()
    scan = await loop.run_in_executor(
        None, lambda: folder.ingest(path, client=CLIENT))
    if scan.indexed:
        try:
            from tools.brief import describe
            for f in scan.files[:3]:
                _remember_brief(chat_id, describe(Path(f), 1))
        except Exception:
            pass
    return {**scan.as_dict(), "index": pipeline.status()}


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


@app.get("/api/source/{name}")
def api_source_get(name: str):
    """
    Serve an indexed source file so the UI can open what it cites.

    The activity feed names the file it worked from - "about
    982_png_jpg.rf.e30...jpg" - and that name was dead text. A reader who
    wants to check the evidence had to go find the file on disk, which is
    exactly the friction citations exist to remove.

    Inline, not as a download: a drawing or a scan should open in the tab.
    The path is pinned to the corpus directory the same way deliverables are.
    """
    from fastapi.responses import FileResponse

    safe = Path(name).name
    path = (pipeline.CORPUS_DIR / safe).resolve()
    if not path.exists() or path.parent != pipeline.CORPUS_DIR.resolve():
        return JSONResponse({"error": "not found"}, status_code=404)
    kind = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff",
        ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=kind)


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
