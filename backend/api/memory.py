"""
What the workbench remembers between sessions.

The document index and the chat history already survive a restart - they are
on disk. What did not was everything the agent uses to know what is CURRENTLY
being worked on: which files were uploaded, which drawing is on the table, the
brief describing each one. Those lived in plain lists in the process, so
closing the terminal wiped them.

That shows up exactly as the user described: upload a P&ID today, come back in
two days, ask "what is in this drawing", and the assistant no longer knows
which drawing you mean - even though the documents are still perfectly
searchable. The knowledge survived; the context did not.

data/session.json - plain JSON, written atomically, same as chats.py.
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


def load() -> dict:
    if not STORE.exists():
        return {}
    try:
        d = json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return {}

    cutoff = time.time() - MAX_AGE_DAYS * 86400
    if d.get("saved", 0) < cutoff:
        return {}

    # Forget anything that has since been deleted or moved.
    for key in ("uploads", "drawings", "images"):
        d[key] = [p for p in d.get(key, []) if Path(p).exists()]
    d["briefs"] = [b for b in d.get("briefs", [])
                   if b.get("path") and Path(b["path"]).exists()]
    if d.get("folder") and not Path(d["folder"]).is_dir():
        d["folder"] = None
    return d


def save(uploads: list[str], drawings: list[str], images: list[str],
         briefs: list, folder: str | None = None) -> None:
    with _lock:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved": time.time(),
            "uploads": list(uploads),
            "drawings": list(drawings),
            "images": list(images),
            "briefs": [asdict(b) if is_dataclass(b) else dict(b)
                       for b in briefs],
            "folder": folder,
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
