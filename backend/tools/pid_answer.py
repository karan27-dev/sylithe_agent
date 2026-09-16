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

# Each tag lookup adds several passages. Past a handful the document block
# drowns the drawing, which is the one thing only the drawing can answer.
MAX_TAGS_LOOKED_UP = 5
MAX_BLOCK_CHARS = 1800


def describe(image: str | Path, conf: float = 0.25) -> dict:
    """Structure only - no model call, no document lookup."""
    r = analyze_pid(image, conf=conf)
    g = r["graph"]

    lines = [f"DRAWING: {Path(image).name}"]
    by_cls: dict[str, list[str]] = {}
    for n, d in g.nodes(data=True):
        by_cls.setdefault(d.get("cls", "unknown"), []).append(n)
    # One item per line, numbered.
    #
    # Grouped as "gate_valve: FV-4033, HV-4021" the model read the line as a
    # single entry and reported only FV-4033 - it dropped a valve from a list
    # of equipment, which tells the reader that valve is not on the drawing.
    # A numbered one-per-line list with an explicit total is much harder to
    # silently shorten than a comma-separated group.
    total = sum(len(v) for v in by_cls.values())
    lines.append(f"\nEQUIPMENT AND INSTRUMENTS FOUND ({total} items - "
                 f"list all {total}):")
    i = 0
    for cls in sorted(by_cls):
        for tag in sorted(by_cls[cls]):
            i += 1
            lines.append(f"  {i}. {tag}  ({cls})")

    if g.number_of_edges():
        lines.append("\nCONNECTED BY PIPE:")
        for u, v in sorted(g.edges()):
            lines.append(f"  {u} -- {v}")

    tagged = [n for n, d in g.nodes(data=True) if d.get("tag")]
    for t in sorted(tagged):
        iso = isolation_valves(g, t)
        if iso:
            # State the count as well. Given only a comma list, the model
            # helpfully added a second valve and labelled it "secondary
            # isolation valve" - which is a fabricated instruction in a safety
            # context. Saying "exactly 1 valve" leaves no room for that.
            lines.append(f"\nTO ISOLATE {t}, CLOSE exactly {len(iso)} "
                         f"valve(s): {', '.join(iso)}")

    # One line per tag, and the question is about ONE of them.
    #
    # tier-S answered "close HV-4021" and stopped. the 4b model reads the whole
    # block and helpfully adds the neighbouring tags' lines too - so asked how
    # to isolate TK-4102 it also named PSV-2041, the tank's only relief path.
    # Telling a technician to close a relief device is a safety error, and the
    # bigger model produced it precisely because it summarised more.
    if tagged:
        lines.append("\nEach TO ISOLATE line above answers exactly one tag. "
                     "Answer only the line for the tag in the question. Do "
                     "not mention other tags' isolation, and never list a "
                     "relief device (PSV/PRV) as something to close.")

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
            blocks.append(f"--- recorded for {tag} ---\n{ctx[:MAX_BLOCK_CHARS]}")
            cited.extend(hits)
        else:
            blocks.append(f"--- recorded for {tag} ---\n(nothing in the documents)")

    # Documents FIRST, drawing LAST.
    #
    # With ten tags looked up, the document block runs to thousands of
    # characters and buried the drawing at the top. Measured: asked what to
    # close to isolate T-501, the model answered "there is no instruction in
    # the text" while the graph directly above it said HV-501 - it had read the
    # reports and never reached the structure. Small models weight the end of a
    # prompt, so the structure now sits immediately before the question.
    text = ("FROM THE DOCUMENTS (background values - these are NOT on the "
            "drawing):\n" + "\n\n".join(blocks)
            + "\n\n" + "=" * 60 + "\n"
            + "THE DRAWING ITSELF - answer questions about equipment, valves, "
              "connections and isolation from THIS section:\n\n"
            + d["text"])
    return {**d, "text": text, "hits": cited}
