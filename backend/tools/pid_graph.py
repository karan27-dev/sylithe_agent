"""
Stage 3 of P&ID understanding: trace the pipes and build a connectivity graph.

Stages 1 and 2 give a bag of tagged symbols. That is still not a P&ID - the
whole value of the drawing is which pipe runs where. This turns the drawing
into a graph so the questions an engineer actually asks become graph queries:

    "what do I close to isolate TK-4102"   -> valves on every path out
    "what is downstream of P-4110A"        -> reachable set
    "how does TK-4102 reach the PSV"       -> shortest path

Why morphology rather than Hough. P&ID pipe runs are orthogonal by drafting
convention, and Hough returns a cloud of overlapping segments that then need
merging anyway. Opening the image with a long horizontal kernel keeps exactly
the horizontal runs and deletes everything else; the same with a vertical
kernel. It is simpler, and it fails in an obvious way rather than a subtle one.

Symbols and text are masked out first. A valve glyph is full of short strokes
and a tag is full of straight edges - left in, both get traced as pipe.

    python -m tools.pid_graph data/corpus/PID-CDU2-004.png
    python -m tools.pid_graph IMAGE --isolate TK-4102
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from tools.pid_ocr import Symbol, TextBox, analyze, read_text

# A pipe run must be at least this fraction of the page to count. Below it we
# are picking up hatching, borders of title blocks and symbol internals.
MIN_RUN_FRAC = 0.02

# An endpoint this close to a symbol (as a multiple of the symbol's size) is
# treated as touching it. Drafting leaves a small gap where a line meets a glyph.
TOUCH_FACTOR = 1.2

# Large equipment needs a larger reach than a valve, for a concrete reason: a
# tag-anchored node sits where the TEXT is, and on a vessel the text is printed
# inside the body while the pipe joins at the edge. Measured on
# PID-CDU2-004.png: TK-4102's label is 161 px from the nearest pipe endpoint
# while its valves are 30-50 px. One reach for both either misses the vessel or
# wires every valve to its neighbour.
BULKY = {"vessel", "tower", "reactor", "fired_heater", "heat_exchanger",
         "pump", "compressor", "turbine", "conveyor", "fan"}
BULKY_FACTOR = 4.0

# Two segments whose endpoints are this close are the same junction.
JOIN_PX = 14

# Valve classes that can actually isolate a line. A check valve stops reverse
# flow but is not something an operator closes, and a relief valve must never
# be listed as an isolation point.
ISOLATING = {"gate_valve", "ball_valve", "globe_valve", "butterfly_valve",
             "plug_valve", "needle_valve", "diaphragm_valve", "angle_valve",
             "control_valve"}


# Tag prefixes follow ISA conventions, so an unmatched tag still tells us what
# kind of thing it is. Only used for tag-anchored nodes, never to override the
# detector.
PREFIX_CLASS = {
    "PSV": "relief_valve", "PRV": "relief_valve", "RV": "relief_valve",
    "HV": "gate_valve", "XV": "gate_valve", "BV": "ball_valve",
    "FV": "control_valve", "PV": "control_valve", "TV": "control_valve",
    "LV": "control_valve", "CV": "check_valve",
    "P": "pump", "C": "compressor", "K": "compressor",
    "TK": "vessel", "V": "vessel", "D": "vessel", "E": "heat_exchanger",
    "F": "fired_heater", "H": "fired_heater", "R": "reactor", "T": "tower",
    # ISA 5.1 letter codes. First letter = measured variable (P/T/F/L),
    # following letters = function (T transmitter, I indicator, C controller,
    # R recorder, A alarm, V valve, Y relay). Tested on real drawings from the
    # Roboflow set: without these, 9 of 22 tags on p-id-Diagrams_26 came back
    # "unknown" - TCV, FCV, TIRC, PAI, TAH, TAL, HS, FY, TY, FIC, FR are all
    # ordinary codes, not exotic ones.
    "LT": "level_instrument", "LI": "level_instrument", "LC": "level_instrument",
    "LG": "level_instrument", "LS": "level_instrument", "LAH": "level_instrument",
    "LAL": "level_instrument", "LIC": "level_instrument", "LY": "level_instrument",
    "PT": "pressure_instrument", "PI": "pressure_instrument",
    "PG": "pressure_instrument", "PC": "pressure_instrument",
    "PAI": "pressure_instrument", "PAH": "pressure_instrument",
    "PAL": "pressure_instrument", "PIC": "pressure_instrument",
    "PR": "pressure_instrument", "PY": "pressure_instrument",
    "PDI": "pressure_instrument", "PDT": "pressure_instrument",
    "FT": "flow_instrument", "FI": "flow_instrument", "FE": "flow_instrument",
    "FR": "flow_instrument", "FIC": "flow_instrument", "FQ": "flow_instrument",
    "FAL": "flow_instrument", "FAH": "flow_instrument", "FY": "flow_instrument",
    "TT": "temp_instrument", "TI": "temp_instrument", "TC": "temp_instrument",
    "TR": "temp_instrument", "TIC": "temp_instrument", "TIRC": "temp_instrument",
    "TAH": "temp_instrument", "TAL": "temp_instrument", "TW": "temp_instrument",
    "TY": "temp_instrument", "TE": "temp_instrument",
    # final control elements - these are valves and DO isolate
    "TCV": "control_valve", "PCV": "control_valve", "LCV": "control_valve",
    "FCV": "control_valve", "SDV": "gate_valve", "ESDV": "gate_valve",
    "MOV": "gate_valve", "ROV": "gate_valve",
    # hand switches and relays are neither equipment nor isolation points
    "HS": "misc_instrument", "XY": "misc_instrument", "ZS": "misc_instrument",
    "AI": "misc_instrument", "AT": "misc_instrument",
    # equipment
    "CT": "tower", "DR": "vessel", "EX": "heat_exchanger", "HX": "heat_exchanger",
}


def _infer_class(tag: str) -> str:
    return PREFIX_CLASS.get(tag.split("-")[0].upper(), "unknown")


@dataclass
class Segment:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def ends(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (self.x1, self.y1), (self.x2, self.y2)


# ---------------------------------------------------------------------------
# line extraction
# ---------------------------------------------------------------------------

def _mask(img, symbols: list[Symbol], texts: list[TextBox], pad: int = 6):
    """Paint over symbols and text so only pipe survives."""
    import cv2
    import numpy as np

    out = img.copy()
    h, w = out.shape[:2]
    for s in symbols:
        x1, y1 = max(0, int(s.x1) - pad), max(0, int(s.y1) - pad)
        x2, y2 = min(w, int(s.x2) + pad), min(h, int(s.y2) + pad)
        cv2.rectangle(out, (x1, y1), (x2, y2), 255, -1)
    for t in texts:
        # OCR gives a centre; blank a generous box around it
        bw, bh = 9 * len(t.text), 34
        x1, y1 = max(0, int(t.cx - bw / 2)), max(0, int(t.cy - bh / 2))
        x2, y2 = min(w, int(t.cx + bw / 2)), min(h, int(t.cy + bh / 2))
        cv2.rectangle(out, (x1, y1), (x2, y2), 255, -1)
    return out


def extract_lines(image: str | Path, symbols: list[Symbol],
                  texts: list[TextBox]) -> list[Segment]:
    import cv2
    import numpy as np

    img = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    h, w = img.shape
    clean = _mask(img, symbols, texts)
    # ink becomes white so morphology keeps it
    bw = cv2.threshold(clean, 0, 255,
                       cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    segs: list[Segment] = []
    for axis, size in (("h", max(12, int(w * MIN_RUN_FRAC))),
                       ("v", max(12, int(h * MIN_RUN_FRAC)))):
        kern = cv2.getStructuringElement(
            cv2.MORPH_RECT, (size, 1) if axis == "h" else (1, size))
        runs = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kern, iterations=1)
        n, _, stats, _ = cv2.connectedComponentsWithStats(runs, 8)
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            if axis == "h" and ww >= size:
                segs.append(Segment(x, y + hh // 2, x + ww, y + hh // 2))
            elif axis == "v" and hh >= size:
                segs.append(Segment(x + ww // 2, y, x + ww // 2, y + hh))
    return segs


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------

def build_graph(symbols: list[Symbol], segments: list[Segment]):
    """
    Nodes are tagged symbols; edges mean "connected by pipe".

    Segments are joined to each other at shared endpoints, then each connected
    run of pipe is asked which symbols it touches. Every pair of symbols on the
    same run becomes an edge.
    """
    import networkx as nx

    g = nx.Graph()
    for s in symbols:
        name = s.tag or f"{s.cls}@{int(s.cx)},{int(s.cy)}"
        g.add_node(name, cls=s.cls, tag=s.tag, x=s.cx, y=s.cy,
                   isolating=s.cls in ISOLATING)

    # union-find over segments that share an endpoint
    parent = list(range(len(segments)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(segments):
        for j in range(i + 1, len(segments)):
            b = segments[j]
            if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= JOIN_PX
                   for p in a.ends() for q in b.ends()):
                union(i, j)

    runs: dict[int, list[Segment]] = {}
    for i, seg in enumerate(segments):
        runs.setdefault(find(i), []).append(seg)

    for run in runs.values():
        touching = []
        for s in symbols:
            name = s.tag or f"{s.cls}@{int(s.cx)},{int(s.cy)}"
            factor = BULKY_FACTOR if s.cls in BULKY else TOUCH_FACTOR
            reach = s.size * factor
            for seg in run:
                if any(math.hypot(p[0] - s.cx, p[1] - s.cy) <= reach
                       for p in seg.ends()):
                    touching.append((s, name))
                    break
        if len(touching) < 2:
            continue

        # Connect them as a CHAIN, not all-to-all.
        #
        # A pipe run is a line, and the things on it sit in order along it -
        # tank, then valve, then pump. Joining every pair instead produced a
        # complete graph: measured on a symbol-legend sheet, 7 symbols became
        # 21 edges, which says everything is connected to everything and
        # therefore says nothing. Ordering along the run's dominant axis and
        # linking only neighbours gives 6 edges and an isolation answer that
        # means something.
        xs = [abs(sg.x2 - sg.x1) for sg in run]
        ys = [abs(sg.y2 - sg.y1) for sg in run]
        horizontal = sum(xs) >= sum(ys)
        touching.sort(key=lambda t: t[0].cx if horizontal else t[0].cy)

        for (s_a, a), (s_b, b) in zip(touching, touching[1:]):
            if a != b:
                g.add_edge(a, b, run=len(run))
    return g


# ---------------------------------------------------------------------------
# queries - the point of the whole exercise
# ---------------------------------------------------------------------------

def isolation_valves(g, tag: str) -> list[str]:
    """
    Valves an operator would close to cut this equipment off.

    The first isolating valve along every path leaving the node - not every
    valve in the plant, and never a relief valve.
    """
    import networkx as nx

    if tag not in g:
        return []
    found, seen, frontier = [], {tag}, [tag]
    while frontier:
        nxt = []
        for n in frontier:
            for m in g.neighbors(n):
                if m in seen:
                    continue
                seen.add(m)
                if g.nodes[m].get("isolating"):
                    found.append(m)       # stop here: everything beyond is cut
                else:
                    nxt.append(m)
        frontier = nxt
    return sorted(found)


def path_between(g, a: str, b: str) -> list[str]:
    import networkx as nx
    if a not in g or b not in g:
        return []
    try:
        return nx.shortest_path(g, a, b)
    except nx.NetworkXNoPath:
        return []


def downstream(g, tag: str) -> list[str]:
    import networkx as nx
    if tag not in g:
        return []
    return sorted(nx.node_connected_component(g, tag) - {tag})


def analyze_pid(image: str | Path, conf: float = 0.25) -> dict:
    """Stages 1 + 2 + 3. This is what the agent's pre_tool will call."""
    r = analyze(image, conf=conf)
    texts = read_text(image)
    symbols = [Symbol(s["class"], s["conf"], *s["box"], tag=s["tag"])
               for s in r["symbols"]]

    # A tag with no symbol under it is still a real node. Detection is the
    # weakest link - the detector is trained on one drawing style and will
    # always miss things - but a tag printed on the page is hard evidence that
    # equipment exists there. Anchor a node on every unclaimed tag so the graph
    # degrades gracefully instead of collapsing to nothing.
    claimed = {s.tag for s in symbols if s.tag}
    for t in r["tag_boxes"]:
        if t["text"] in claimed:
            continue
        half = 34.0
        symbols.append(Symbol(_infer_class(t["text"]), 0.0,
                              t["cx"] - half, t["cy"] - half,
                              t["cx"] + half, t["cy"] + half, tag=t["text"]))
        claimed.add(t["text"])
    segs = extract_lines(image, symbols, texts)
    g = build_graph(symbols, segs)
    return {
        **r,
        "segments": len(segs),
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "graph": g,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--isolate", help="list isolation valves for this tag")
    ap.add_argument("--path", nargs=2, metavar=("FROM", "TO"))
    a = ap.parse_args()

    r = analyze_pid(a.image, conf=a.conf)
    g = r["graph"]
    print(f"{Path(a.image).name}")
    print(f"  detector : {r['detector']}")
    print(f"  symbols  : {len(r['symbols'])}   tags: {len(r['tags'])}")
    print(f"  pipe runs: {r['segments']} segments")
    print(f"  graph    : {r['nodes']} nodes, {r['edges']} edges")
    if g.number_of_nodes():
        print("\n  connections:")
        for u, v in sorted(g.edges()):
            print(f"    {u}  --  {v}")
    if a.isolate:
        print(f"\n  to isolate {a.isolate}: {isolation_valves(g, a.isolate) or 'no path found'}")
    if a.path:
        p = path_between(g, *a.path)
        print(f"\n  {a.path[0]} -> {a.path[1]}: {' -> '.join(p) if p else 'no path'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
