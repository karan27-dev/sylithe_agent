"""
Score the P&ID pipeline against data/bench_pid, tier by tier.

Three things are measured separately, because they fail for different reasons
and a single number hides which one is broken:

  detection    did YOLO find the symbol at all, and call it the right class
  tags         did OCR read the tag, so the symbol has a name
  connectivity did line tracing join the right pairs

Run:  python -m bench.run_pid_bench            (full pipeline, as shipped)
      python -m bench.run_pid_bench --no-tile  (control: single pass)
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
BENCH_SYNTH = _ROOT / "data" / "bench_pid"          # tags + connectivity
BENCH_REAL = _ROOT / "data" / "bench_pid_real"      # detection, real pixels
BENCH = BENCH_REAL
IOU = 0.5


def iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    ar = lambda z: (z[2] - z[0]) * (z[3] - z[1])
    return inter / (ar(a) + ar(b) - inter)


def match(pred: list[dict], gt: list[dict]) -> tuple[int, int]:
    """Greedy highest-IoU-first. Returns (located, located AND right class)."""
    pairs = sorted(((iou(p["box"], g["box"]), i, j)
                    for i, p in enumerate(pred) for j, g in enumerate(gt)),
                   key=lambda t: -t[0])
    up, ug, hit, cls_hit = set(), set(), 0, 0
    for v, i, j in pairs:
        if v < IOU:
            break
        if i in up or j in ug:
            continue
        up.add(i); ug.add(j); hit += 1
        if pred[i]["class"] == gt[j]["class"]:
            cls_hit += 1
    return hit, cls_hit


def prf(tp: int, npred: int, ngt: int) -> tuple[float, float, float]:
    r = tp / ngt if ngt else 0.0
    p = tp / npred if npred else 0.0
    return r, p, (2 * r * p / (r + p) if r + p else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tile", action="store_true",
                    help="control run: one pass over the whole sheet")
    ap.add_argument("--tier", help="score one tier only")
    ap.add_argument("--synth", action="store_true",
                    help="score the synthetic set (tags and connectivity) "
                         "instead of the real-pixel detection set")
    a = ap.parse_args()

    import sys
    sys.path.insert(0, str(_ROOT))
    from tools import pid_ocr
    from tools.pid_graph import analyze_pid

    if a.no_tile:                       # the control the tiling claim rests on
        pid_ocr.TILE_ABOVE = 10 ** 9

    global BENCH
    BENCH = BENCH_SYNTH if a.synth else BENCH_REAL
    truth = json.loads((BENCH / "ground_truth.json").read_text())
    if a.tier:
        truth = [t for t in truth if t["tier"] == a.tier]

    agg: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    print(f"{'sheet':15} {'size':11} {'sym':>4} {'found':>6} {'class':>6} "
          f"{'tags':>6} {'edges':>6} {'sec':>6}")
    print("-" * 70)

    for t in truth:
        gt = t["symbols"]
        gt_tags = {s["tag"] for s in gt if s.get("tag")}
        gt_edges = {frozenset(e) for e in t.get("edges", [])}
        t0 = time.perf_counter()
        r = analyze_pid(BENCH / t["sheet"], conf=0.25)
        dt = time.perf_counter() - t0

        pred = [{"class": s["class"], "box": s["box"]} for s in r["symbols"]]
        hit, cls_hit = match(pred, gt)
        g = r["graph"]
        tag_hit = len(gt_tags & set(g.nodes()))
        edge_hit = len({frozenset(e) for e in g.edges()} & gt_edges)

        k = t["tier"]
        agg[k]["sheets"] += 1
        agg[k]["gt"] += len(gt); agg[k]["pred"] += len(pred)
        agg[k]["hit"] += hit;    agg[k]["cls"] += cls_hit
        agg[k]["gt_tags"] += len(gt_tags); agg[k]["tag_hit"] += tag_hit
        agg[k]["gt_edges"] += len(gt_edges); agg[k]["edge_hit"] += edge_hit
        agg[k]["pred_edges"] += g.number_of_edges()
        agg[k]["sec"] += dt

        print(f"{t['sheet'][:-4]:15} {t['width']}x{t['height']:<5} "
              f"{len(gt):4} {hit:6} {cls_hit:6} {tag_hit:6} {edge_hit:6} "
              f"{dt:6.1f}")

    mode = "SINGLE PASS (control)" if a.no_tile else "TILED (as shipped)"
    print(f"\n{'=' * 70}\n{mode}\n")
    print(f"{'tier':11} {'sheets':>6} {'symbols':>8} {'locate':>8} "
          f"{'class':>8} {'tags':>8} {'edges F1':>9} {'s/sheet':>8}")
    print("-" * 70)
    for k in ("easy", "hard", "very_hard", "complex"):
        v = agg.get(k)
        if not v:
            continue
        lr, lp, _ = prf(v["hit"], v["pred"], v["gt"])
        cr, _, _ = prf(v["cls"], v["pred"], v["gt"])
        _, _, ef = prf(v["edge_hit"], v["pred_edges"], v["gt_edges"])
        print(f"{k:11} {int(v['sheets']):6} {int(v['gt']):8} "
              f"{lr * 100:7.1f}% {cr * 100:7.1f}% "
              f"{(v['tag_hit'] / v['gt_tags'] * 100) if v['gt_tags'] else float('nan'):7.1f}% {ef:9.3f} "
              f"{v['sec'] / v['sheets']:8.1f}")

    tot = {m: sum(v[m] for v in agg.values())
           for m in ("gt", "pred", "hit", "cls", "gt_tags", "tag_hit",
                     "gt_edges", "edge_hit", "pred_edges", "sec", "sheets")}
    lr, lp, lf = prf(tot["hit"], tot["pred"], tot["gt"])
    cr, _, _ = prf(tot["cls"], tot["pred"], tot["gt"])
    _, _, ef = prf(tot["edge_hit"], tot["pred_edges"], tot["gt_edges"])
    print("-" * 70)
    print(f"{'ALL':11} {int(tot['sheets']):6} {int(tot['gt']):8} "
          f"{lr * 100:7.1f}% {cr * 100:7.1f}% "
          f"{(tot['tag_hit'] / tot['gt_tags'] * 100) if tot['gt_tags'] else float('nan'):7.1f}% {ef:9.3f} "
          f"{tot['sec'] / tot['sheets']:8.1f}")
    print(f"\nlocalisation precision {lp * 100:.1f}%, F1 {lf:.3f} "
          f"(IoU {IOU}, class-agnostic)")

    out = BENCH / ("results_notile.json" if a.no_tile else "results.json")
    out.write_text(json.dumps({k: dict(v) for k, v in agg.items()}, indent=1))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
