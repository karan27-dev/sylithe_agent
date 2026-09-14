"""
Turn a P&ID into text the reasoning lane can actually use.

The graph is the hard part; this is the small piece that makes it answerable.
It renders the drawing's structure as a compact block of text and, crucially,
joins every tag back to the indexed documents - because the drawing carries
symbols, tags and topology, while the NUMBERS live in the reports.

That join is the whole point of the P&ID work:

    P&ID     ->  "PSV-2041 sits on TK-4102"      (structure)
    documents->  "PSV-2041 is set at 12.5 barg"  (value)
"""

from __future__ import annotations

from pathlib import Path

from tools.pid_graph import (analyze_pid, isolation_valves, downstream,
                             path_between)

MAX_TAGS_LOOKED_UP = 8


def describe(image: str | Path, conf: float = 0.25) -> dict:
    """Structure only - no model call, no document lookup."""
    r = analyze_pid(image, conf=conf)
    g = r["graph"]

    lines = [f"DRAWING: {Path(image).name}"]
    by_cls: dict[str, list[str]] = {}
    for n, d in g.nodes(data=True):
        by_cls.setdefault(d.get("cls", "unknown"), []).append(n)
    lines.append("\nEQUIPMENT AND INSTRUMENTS FOUND:")
    for cls in sorted(by_cls):
        lines.append(f"  {cls}: {', '.join(sorted(by_cls[cls]))}")

    if g.number_of_edges():
        lines.append("\nCONNECTED BY PIPE:")
        for u, v in sorted(g.edges()):
            lines.append(f"  {u} -- {v}")

    tagged = [n for n, d in g.nodes(data=True) if d.get("tag")]
    for t in sorted(tagged):
        iso = isolation_valves(g, t)
        if iso:
            lines.append(f"\nTO ISOLATE {t}, CLOSE: {', '.join(iso)}")

    return {"text": "\n".join(lines), "graph": g, "raw": r,
            "tags": sorted(tagged)}


def with_values(image: str | Path, client=None, k: int = 3,
                conf: float = 0.25) -> dict:
    """
    Structure plus the recorded values for each tag.

    A P&ID never states a set pressure or a thickness; it states a tag. So each
    tag is looked up in the corpus and whatever the documents say is attached.
    Tags with nothing on record are listed as such rather than invented.
    """
    from ingest.pipeline import context

    d = describe(image, conf=conf)
    blocks, cited = [], []
    for tag in d["tags"][:MAX_TAGS_LOOKED_UP]:
        try:
            ctx, hits = context(f"{tag} set pressure design thickness "
                                f"specification inspection", k, client=client)
        except Exception:
            ctx, hits = "", []
        if ctx:
            blocks.append(f"--- recorded for {tag} ---\n{ctx}")
            cited.extend(hits)
        else:
            blocks.append(f"--- recorded for {tag} ---\n(nothing in the documents)")

    text = d["text"] + "\n\nFROM THE DOCUMENTS (values are NOT on the drawing):\n" \
           + "\n\n".join(blocks)
    return {**d, "text": text, "hits": cited}
