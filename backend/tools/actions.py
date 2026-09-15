"""
Extract action-item rows (Action / Owner / Due, or similar) from indexed
tables, without letting a language model touch the values.

Root cause of the failure this fixes: a "list pending actions" question was
answered from vector search over ALL chunks, and the header chunk of a
bulletin ("Subject: ... Issued: ... Department") scores just as high as its
own action table for a generic query - sometimes higher, since two revisions
of the same bulletin have near-identical headers that crowd out everything
else in a fixed top-k. The table chunk that actually holds the rows never
made it into the retrieved set, so the model correctly said "no actions
found" - it was telling the truth about what it was shown, which was the
wrong evidence.

This bypasses ranked retrieval entirely for this task. ingest.pipeline
already parses every table with do_table_structure=True and exports each
cell through docling's chunker as literal text in the shape
"<row>, <ColumnHeader> = <value>. " (verified against the real index - see
_ROW_CELL below). That is a fixed, parseable format, not model output, so
rows are read out of it with a regex, never generated. A row is never
invented: if no table chunk mentions the requested tag, the result is empty
and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingest import pipeline

# docling's table-to-text export, per cell: "<row>, <Column> = <value>. "
# Row numbers are 1-based and reset per table. Verified against real indexed
# text, e.g.:
#   "1, Action = Reset PSV-201 to 25.0 barg. 1, Owner = Maintenance. "
#   "1, Due = 30-Sep-2026. 2, Action = Survey E-204 ..."
_ROW_CELL = re.compile(
    r"(\d+),\s*([A-Za-z][A-Za-z0-9 _/-]*?)\s*=\s*(.*?)"
    r"(?=\s*\d+,\s*[A-Za-z][A-Za-z0-9 _/-]*?\s*=|\s*$)",
    re.S,
)

# A table counts as "action-like" only if it has at least one column whose
# header suggests a task, and the row-owner/row-due columns most people mean
# by "action tracker". Requiring ACTION_COL avoids treating an unrelated
# table (e.g. a UT-thickness log) as an action list just because it also
# happens to be table-kind.
_ACTION_COL = re.compile(r"action|task|item|instruction", re.I)
_OWNER_COL = re.compile(r"owner|responsible|assign", re.I)
_DUE_COL = re.compile(r"due|deadline|date", re.I)


@dataclass
class ActionRow:
    row: int
    fields: dict[str, str]
    source: str
    page: int
    heading: str

    @property
    def action(self) -> str:
        return next((v for k, v in self.fields.items() if _ACTION_COL.search(k)), "")

    @property
    def owner(self) -> str:
        return next((v for k, v in self.fields.items() if _OWNER_COL.search(k)), "")

    @property
    def due(self) -> str:
        return next((v for k, v in self.fields.items() if _DUE_COL.search(k)), "")

    def cite(self) -> str:
        loc = f"p.{self.page}" if self.page else "—"
        return f"{self.source} ({loc})"


def _parse_table_cells(text: str) -> dict[int, dict[str, str]]:
    """"1, Action = X. 1, Owner = Y." -> {1: {"Action": "X", "Owner": "Y"}}."""
    rows: dict[int, dict[str, str]] = {}
    for m in _ROW_CELL.finditer(text or ""):
        row_no = int(m.group(1))
        col = m.group(2).strip()
        val = m.group(3).strip().rstrip(".")
        if not col or not val:
            continue
        rows.setdefault(row_no, {})[col] = val
    return rows


def _all_chunks() -> list[pipeline.Chunk]:
    """Every chunk in the index, of any kind."""
    db = pipeline._db()
    if pipeline.TABLE not in pipeline._tables(db):
        return []
    tbl = db.open_table(pipeline.TABLE)
    rows = tbl.search().limit(100_000).to_list()
    return [
        pipeline.Chunk(chunk_id=r["chunk_id"], source=r["source"], path=r["path"],
                        page=int(r["page"]), heading=r["heading"], kind=r["kind"],
                        text=r["text"])
        for r in rows
    ]


def _sources_mentioning(tag: str, chunks: list[pipeline.Chunk]) -> set[str]:
    """
    Which source documents mention `tag` ANYWHERE - not just in the row that
    is being filtered. A bulletin's subject line ("Interim monitoring for
    TK-4102") is a separate chunk from its action table, and a row like
    "Bench test and reset PSV-2041" never spells out TK-4102 even though the
    whole bulletin is about it. Scoping to the row text alone made every row
    in a real, on-topic bulletin invisible to a tag-filtered query - checked
    against the actual index, not assumed.
    """
    return {c.source for c in chunks if pipeline._mentions(c.text, tag)
            or pipeline._mentions(c.heading, tag)}


def extract_actions(tag: str | None = None) -> list[ActionRow]:
    """
    All action-like rows in the index, optionally filtered to rows from a
    document that mentions `tag` (an equipment tag such as "TK-4102",
    matched the same way ingest.pipeline matches tags elsewhere).

    Filtering happens at DOCUMENT level, not row level. A row like "Bench
    test and reset PSV-2041" never repeats the tank tag even though the
    bulletin's subject line says "Interim monitoring for TK-4102" - checked
    against the real index. Requiring the tag inside each row's own text
    made every row in an on-topic bulletin invisible. The tradeoff: a
    bulletin covering several unrelated tags would return all of its rows
    for a query naming any one of them. Not a problem in this corpus, where
    each bulletin covers one piece of equipment - worth revisiting if a
    multi-equipment bulletin shows up.
    """
    chunks = _all_chunks()
    wanted_sources = _sources_mentioning(tag, chunks) if tag else None

    out: list[ActionRow] = []
    for chunk in chunks:
        if chunk.kind != "table":
            continue
        if wanted_sources is not None and chunk.source not in wanted_sources:
            continue
        cols_seen = {c for row in _parse_table_cells(chunk.text).values() for c in row}
        if not any(_ACTION_COL.search(c) for c in cols_seen):
            continue          # not an action-shaped table - e.g. a UT log
        for row_no, fields in sorted(_parse_table_cells(chunk.text).items()):
            out.append(ActionRow(row=row_no, fields=fields, source=chunk.source,
                                  page=chunk.page, heading=chunk.heading))
    return out


def format_table(rows: list[ActionRow]) -> str:
    if not rows:
        return ""
    lines = ["| Action | Owner | Due | Source |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.action} | {r.owner} | {r.due} | {r.cite()} |")
    return "\n".join(lines)
