"""
Compare two documents (or two revisions of the same one) by diffing their
actual text, not by asking a language model to remember what it retrieved.

Root cause of the failure this fixes: "compare X with its REV 2" went
through ordinary retrieval. Two documents that share most of their wording
("Maintenance Bulletin MB-2026-17" vs "...MB-2026-22") embed close enough
together that an unrelated bulletin's table won out for a retrieval slot
over the actual REV 2's own action table - checked against the real
retrieval scores, not assumed. The model then had passages from THREE
documents in front of it and no signal for which two the question meant, so
it merged an unrelated bulletin's rows into the answer, and separately
reported an unchanged line as if it were new because both copies of that
line happened to be missing from what it was shown.

This tool removes both failure modes by construction: it identifies the two
named documents from the corpus's own indexed titles (not a similarity
search across every chunk), reads each one's FULL text directly from disk,
and diffs that text with Python's difflib. A line is reported as changed
only if it demonstrably differs between the two files. The model is not
asked to detect differences - only, optionally, to phrase a list it did not
produce.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ingest import pipeline

_WORD = re.compile(r"[a-z0-9]+", re.I)
# Numbers are what actually distinguish "MB-2026-17" from "MB-2026-22", and
# what distinguish REV 2 of one bulletin from REV 2 of another. Weighting
# them higher than ordinary words is what keeps "Maintenance Bulletin"
# (shared by every bulletin in the corpus) from swamping the one token that
# tells two candidates apart.
_NUMERIC_WEIGHT = 3


@dataclass
class DocInfo:
    source: str
    path: str
    title: str


@dataclass
class DiffLine:
    kind: str          # "changed" | "added" | "removed"
    old: str = ""
    new: str = ""


@dataclass
class CompareResult:
    doc_a: DocInfo
    doc_b: DocInfo
    changes: list[DiffLine] = field(default_factory=list)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text or "")]


def _catalog() -> list[DocInfo]:
    """One entry per indexed source file, titled by its first heading (or,
    failing that, its own first line of text)."""
    db = pipeline._db()
    if pipeline.TABLE not in pipeline._tables(db):
        return []
    tbl = db.open_table(pipeline.TABLE)
    rows = tbl.search().limit(100_000).to_list()
    by_source: dict[str, dict] = {}
    for r in rows:
        d = by_source.setdefault(r["source"], {"path": r["path"], "title": None})
        if d["title"] is None:
            d["title"] = r["heading"] or r["text"][:80]
    return [DocInfo(source=s, path=v["path"], title=v["title"] or s)
            for s, v in by_source.items()]


def _score(query_tokens: list[str], candidate: DocInfo) -> int:
    cand_tokens = set(_tokenize(candidate.title) + _tokenize(candidate.source))
    score = 0
    for t in query_tokens:
        if t in cand_tokens:
            score += _NUMERIC_WEIGHT if t.isdigit() else 1
    return score


def find_documents(query: str, top_n: int = 2, min_score: int = 3) -> list[DocInfo]:
    """
    The `top_n` best-scoring distinct documents for a query, provided each
    clears `min_score` - a document that only shares generic words ("bulletin",
    "maintenance") with the query and no identifying number is not a match,
    it is noise, and returning it would recreate the exact mixing bug this
    tool exists to avoid.
    """
    q = _tokenize(query)
    scored = sorted(
        ((c, _score(q, c)) for c in _catalog()),
        key=lambda cs: -cs[1],
    )
    matches = [c for c, s in scored[:top_n] if s >= min_score]
    # Oldest first, so a diff always reads as "what changed going from the
    # earlier document to the later one" - not the order match-scoring
    # happened to produce. Checked against a real answer: with REV 2 scored
    # first (it shares more query tokens - "rev", "2" - with a query that
    # names it), every change came out backwards - "temperature changed from
    # 60 to 65", when the revision actually LOWERED it from 65 to 60, and an
    # action ADDED in the revision was described as "removed". Mtime is a
    # weaker signal than a real revision number would be, but nothing in the
    # corpus exposes one generically, and a later file on disk being the
    # later revision is true for every real case here.
    if len(matches) == 2:
        matches.sort(key=lambda d: Path(d.path).stat().st_mtime)
    return matches


# ---------------------------------------------------------------------------
# text extraction - read the real file, not the chunked/embedded copy
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def extract_text(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".txt", ".csv"):
        return p.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        return _TAG_RE.sub("", raw)
    if suffix == ".docx":
        import docx
        d = docx.Document(str(p))
        parts = [para.text for para in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    # Fallback for kinds this tool does not have a native reader for (pdf,
    # pptx, xlsx, images): reconstruct from what is already indexed. Weaker -
    # chunk order is retrieval-table insertion order, not document order -
    # so this is a best-effort path, not the primary one.
    hits = pipeline.search(p.stem, k=200, source=p.name)
    return "\n".join(h.chunk.text for h in hits)


_STRUCTURAL_LINE = re.compile(
    r"^\s*(#{1,6}\s|[-*]\s|\d+[.)]\s|\|.*\|\s*$|\*\*[^*]+:\*\*)")


def _paragraphs(text: str) -> list[str]:
    """
    Reflow wrapped prose into one diff unit per paragraph, so a sentence
    that happens to wrap at a different column in each file - ordinary word
    wrap, not a content change - is compared whole rather than fragment by
    fragment. Checked against a real diff: the same sentence wrapped after a
    different word in each file, comparing raw source lines split it into
    three fragments that shared almost no text with each other, and the
    model reading those fragments invented a change ("nominal 12.0 mm
    changed to 10.4 mm") that never happened - the paragraph's actual
    content differed nowhere except where the line breaks fell.

    Headings, list items, table rows and "**Label:** value" metadata lines
    stay one unit per line - folding those together would lose exactly the
    boundary (which row, which field) that makes each one meaningful.
    """
    paras: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            paras.append(" ".join(buf))
            buf.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if _STRUCTURAL_LINE.match(line):
            flush()
            paras.append(line)
        else:
            buf.append(line)
    flush()
    return paras


def diff_documents(a: DocInfo, b: DocInfo) -> CompareResult:
    text_a = extract_text(a.path)
    text_b = extract_text(b.path)
    lines_a = _paragraphs(text_a)
    lines_b = _paragraphs(text_b)

    changes: list[DiffLine] = []
    sm = difflib.SequenceMatcher(a=lines_a, b=lines_b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_block = lines_a[i1:i2]
        new_block = lines_b[j1:j2]
        if tag == "replace":
            for o, n in zip(old_block, new_block):
                changes.append(DiffLine(kind="changed", old=o, new=n))
            # Unequal-length replace: leftover lines are pure add/remove.
            for o in old_block[len(new_block):]:
                changes.append(DiffLine(kind="removed", old=o))
            for n in new_block[len(old_block):]:
                changes.append(DiffLine(kind="added", new=n))
        elif tag == "delete":
            for o in old_block:
                changes.append(DiffLine(kind="removed", old=o))
        elif tag == "insert":
            for n in new_block:
                changes.append(DiffLine(kind="added", new=n))
    return CompareResult(doc_a=a, doc_b=b, changes=changes)


def format_changes(result: CompareResult) -> str:
    if not result.changes:
        return (f"No line-level differences found between {result.doc_a.source} "
                f"and {result.doc_b.source}.")
    lines = [f"VERIFIED DIFFERENCES between {result.doc_a.source} (A) and "
             f"{result.doc_b.source} (B) - from a direct line diff, not a summary:"]
    for c in result.changes:
        if c.kind == "changed":
            lines.append(f"- CHANGED: \"{c.old}\"  ->  \"{c.new}\"")
        elif c.kind == "added":
            lines.append(f"- ADDED in B (not in A): \"{c.new}\"")
        else:
            lines.append(f"- REMOVED in B (present in A): \"{c.old}\"")
    return "\n".join(lines)
