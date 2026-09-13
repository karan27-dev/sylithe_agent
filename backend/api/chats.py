"""
Conversation store - multi-turn chat, on disk, entirely local.

Every question used to be independent, so a follow-up like "and the valve
before it?" simply did not work. A conversational interface needs history,
and that history has to survive a restart.

data/chats.json - plain JSON, no database. One engineer's chat history is
never large enough to justify standing up Postgres for it.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent          # backend/
STORE = _ROOT / "data" / "chats.json"
_lock = threading.Lock()

# How much history to send the model. The 2b context window is small and
# every extra turn slows generation, so 6 messages (3 turns) is enough to
# resolve a follow-up.
HISTORY_TURNS = 6


def _load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text())
        except json.JSONDecodeError:
            pass
    return {"chats": {}, "order": []}


def _save(db: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=1))
    tmp.replace(STORE)          # atomic: a crash mid-write leaves the old file intact


def list_chats() -> list[dict]:
    db = _load()
    out = []
    for cid in db["order"]:
        c = db["chats"].get(cid)
        if c:
            out.append({"id": cid, "title": c["title"], "updated": c["updated"],
                        "n": len(c["messages"])})
    return out


def create(title: str = "New chat") -> str:
    with _lock:
        db = _load()
        cid = uuid.uuid4().hex[:12]
        db["chats"][cid] = {"title": title, "created": time.time(),
                            "updated": time.time(), "messages": []}
        db["order"].insert(0, cid)
        _save(db)
    return cid


def get(cid: str) -> dict | None:
    return _load()["chats"].get(cid)


def append(cid: str, role: str, content: str, extra: dict | None = None) -> None:
    with _lock:
        db = _load()
        c = db["chats"].get(cid)
        if c is None:
            return
        msg = {"role": role, "content": content, "ts": time.time()}
        if extra:
            msg.update(extra)
        c["messages"].append(msg)
        c["updated"] = time.time()
        # the first user message becomes the chat title
        if role == "user" and c["title"] == "New chat":
            c["title"] = (content[:44] + "…") if len(content) > 44 else content
        if cid in db["order"]:
            db["order"].remove(cid)
        db["order"].insert(0, cid)
        _save(db)


def rename(cid: str, title: str) -> None:
    with _lock:
        db = _load()
        if cid in db["chats"]:
            db["chats"][cid]["title"] = title[:80]
            _save(db)


def delete(cid: str) -> None:
    with _lock:
        db = _load()
        db["chats"].pop(cid, None)
        if cid in db["order"]:
            db["order"].remove(cid)
        _save(db)


def history(cid: str, turns: int = HISTORY_TURNS) -> list[dict]:
    """
    Messages shaped for the model. Only role + content; the rest of the
    metadata (sources, latency) belongs to the UI, not the model.
    """
    c = get(cid)
    if not c:
        return []
    msgs = [m for m in c["messages"] if m["role"] in ("user", "assistant")]
    return [{"role": m["role"], "content": m["content"]} for m in msgs[-turns:]]
