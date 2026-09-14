"""
Work out what a file actually IS, right after it is uploaded.

The agent used to receive a file path and nothing else, and only looked at it
when routing happened to say "pid" or "vision". Ask "what is this" straight
after uploading a P&ID and it replied "I cannot see or access any files you
have uploaded" - which was false, and the worst kind of false, because the file
was sitting right there.

An assistant should know what is on the table without being told. This builds a
one-paragraph brief at upload time - kind, tags, a content preview - which then
travels with every question in the conversation, so the model never has to be
informed of something it already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DRAWING_EXT = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
TAG = re.compile(r"\b([A-Z]{1,4})[-\s]?(\d{2,6})([A-Z])?\b")


@dataclass
class Brief:
    name: str
    path: str
    kind: str                 # drawing | scan | document | spreadsheet | slides
    chunks: int
    tags: list[str] = field(default_factory=list)
    preview: str = ""
    detail: str = ""

    def line(self) -> str:
        bits = [f"{self.name} ({self.kind})"]
        if self.tags:
            bits.append("mentions " + ", ".join(self.tags[:8]))
        if self.detail:
            bits.append(self.detail)
        return " - ".join(bits)

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "tags": self.tags,
                "detail": self.detail, "chunks": self.chunks}


def _kind(path: Path, chunks: int) -> str:
    ext = path.suffix.lower()
    if ext in DRAWING_EXT:
        # An image that yields no text is a line drawing: its content is
        # geometry. One that yields text is a scan of a printed or written page.
        return "drawing" if chunks == 0 else "scan"
    if ext == ".pdf":
        return "scanned document" if chunks == 0 else "document"
    if ext in (".xlsx", ".csv"):
        return "spreadsheet"
    if ext == ".pptx":
        return "slides"
    return "document"


def describe(path: Path, chunks: int, client=None) -> Brief:
    """Cheap by design - this runs on the upload path, not on a question."""
    path = Path(path)
    kind = _kind(path, chunks)
    b = Brief(name=path.name, path=str(path), kind=kind, chunks=chunks)

    if kind == "drawing":
        # Read the tags off it. This is the only thing that makes a drawing
        # answerable at all, and it is what the user will ask about.
        try:
            from tools.pid_ocr import analyze
            r = analyze(path)
            b.tags = r.get("tags", [])[:12]
            n = len(r.get("symbols", []))
            b.detail = (f"engineering drawing, {n} symbols detected, "
                        f"{len(r.get('tags', []))} tags read")
        except Exception as exc:
            b.detail = f"engineering drawing (not yet analysed: {type(exc).__name__})"
        return b

    # Everything else is indexed, so look at what actually went in.
    try:
        from ingest import pipeline
        db = pipeline._db()
        if pipeline.TABLE in pipeline._tables(db):
            rows = [r for r in db.open_table(pipeline.TABLE)
                    .search().limit(100000).to_list()
                    if r.get("source") == path.name]
            text = " ".join(r.get("text", "") for r in rows)
            seen = []
            for m in TAG.finditer(text.upper()):
                t = f"{m.group(1)}-{m.group(2)}{m.group(3) or ''}"
                if t not in seen:
                    seen.append(t)
            b.tags = seen[:12]
            b.preview = text[:400]
            pages = {r.get("page") for r in rows if r.get("page")}
            b.detail = f"{chunks} indexed passages" + (
                f" over {len(pages)} page(s)" if pages else "")
    except Exception:
        pass
    return b


def context_block(briefs: list[Brief]) -> str:
    """The paragraph handed to the model with every question."""
    if not briefs:
        return ""
    lines = ["RECENTLY UPLOADED BY THE USER (newest first):"]
    for b in briefs:
        lines.append(f"  - {b.line()}")
    lines.append("When the user says 'this file', 'this drawing', 'it' or "
                 "'what did I upload', they mean the newest of these. You DO "
                 "have it - never say you cannot see uploaded files.")
    return "\n".join(lines)
