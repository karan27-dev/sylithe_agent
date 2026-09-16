"""
What the workbench remembers between sessions, PER CHAT.

The document index and the chat history already survive a restart - they are
on disk. What did not was everything the agent uses to know what is CURRENTLY
being worked on: which files were uploaded, which drawing is on the table, the
brief describing each one. Those lived in plain lists in the process, so
closing the terminal wiped them.

That shows up exactly as the user described: upload a P&ID today, come back in
two days, ask "what is in this drawing", and the assistant no longer knows
which drawing you mean - even though the documents are still perfectly
searchable. The knowledge survived; the context did not.

THEN THE OPPOSITE BUG. Those lists were module-level globals - one set for the
whole server - so every chat shared one working context. Open a brand new chat
and the assistant still answered about the file from the previous one, because
there was only ever one "current file" in the process. A chat that starts clean
on screen must start clean underneath, and a chat you return to must still know
what it was working on. Both require the same thing: context keyed by chat.

data/session.json - plain JSON, written atomically, same as chats.py:

    {"saved": 1758...,
     "chats": {"<chat_id>": {"uploads": [...], "drawings": [...],
                             "images": [...], "briefs": [...],
                             "folder": "..."}}}
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "session.json"
_lock = threading.Lock()

# A path that no longer exists is worse than no memory at all, so entries are
# dropped on load rather than pointing the agent at a deleted file.
MAX_AGE_DAYS = 30

# Chats are cheap to keep and the payload is small, but an unbounded file that
# is rewritten on every upload is not. Oldest-written chats fall off.
MAX_CHATS = 60


def _clean(d: dict) -> dict:
    """Forget anything that has since been deleted or moved."""
    out = dict(d)
    for key in ("uploads", "drawings", "images"):
        out[key] = [p for p in out.get(key, []) if Path(p).exists()]
    out["briefs"] = [b for b in out.get("briefs", [])
                     if b.get("path") and Path(b["path"]).exists()]
    if out.get("folder") and not Path(out["folder"]).is_dir():
        out["folder"] = None
    return out


def load() -> dict[str, dict]:
    """All chats' working context, keyed by chat id."""
    if not STORE.exists():
        return {}
    try:
        d = json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return {}

    if d.get("saved", 0) < time.time() - MAX_AGE_DAYS * 86400:
        return {}

    chats = d.get("chats")
    if chats is None:
        # A file written before context was per-chat. It holds one unlabelled
        # working set, and there is no way to know which chat it belonged to.
        # Guessing would reintroduce the bug this format exists to fix, so it
        # is dropped; the documents and the chat transcripts are untouched.
        return {}
    return {cid: _clean(v) for cid, v in chats.items() if isinstance(v, dict)}


def save(chats: dict[str, dict]) -> None:
    with _lock:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        trimmed = dict(list(chats.items())[-MAX_CHATS:])
        payload = {
            "saved": time.time(),
            "chats": {
                cid: {
                    "uploads": list(v.get("uploads", [])),
                    "drawings": list(v.get("drawings", [])),
                    "images": list(v.get("images", [])),
                    "briefs": [asdict(b) if is_dataclass(b) else dict(b)
                               for b in v.get("briefs", [])],
                    "folder": v.get("folder"),
                }
                for cid, v in trimmed.items()
            },
        }
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(STORE)


def restore_briefs(raw: list[dict]) -> list:
    from tools.brief import Brief
    out = []
    for b in raw:
        try:
            out.append(Brief(**{k: v for k, v in b.items()
                                if k in Brief.__dataclass_fields__}))
        except Exception:
            continue
    return out
