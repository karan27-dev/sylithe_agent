"""
Benchmark A - detection at scale, built only from real drawing pixels.

WHY THIS EXISTS ALONGSIDE make_pid_bench.py
    The synthetic builder composes individual cropped symbols onto a blank
    sheet. Measured, that costs the detector a lot: on the ORIGINAL held-out
    test sheets it locates 92.3% of symbols, but on a sheet built from the
    same crops pasted onto white it finds 58%. A lone glyph on an empty page
    is not what the model was trained on, and no paste mode removes that gap
    (opaque 7/12, native 4/12, resized 4/12, thresholded 2/12). So a composited
    sheet cannot fairly score detection - it scores the composition.

    Here nothing is composed at symbol level. Whole held-out test sheets are
    laid out as panels on one large canvas, so every pixel and every bit of
    local context is real drawing, and the ground truth is the dataset's own
    annotations shifted by the panel offset. The only synthetic thing is the
    arrangement - which is exactly the variable under test: does the pipeline
    still work when the sheet is big?

Difficulty is the symbol count, reached by adding panels.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
SRC = _ROOT / "data" / "pid_yolo" / "test"          # never trained on
OUT = _ROOT / "data" / "bench_pid_real"

TIERS = {                       # symbols per sheet, panels across
    "easy":      dict(n=(6, 9),   cols=2, sheets=5),
    "hard":      dict(n=(12, 20), cols=3, sheets=5),
    "very_hard": dict(n=(22, 30), cols=4, sheets=5),
    "complex":   dict(n=(34, 48), cols=6, sheets=5),
}


def panels() -> list[dict]:
    ann = json.loads((SRC / "_annotations.coco.json").read_text())
    names = {c["id"]: c["name"] for c in ann["categories"]}
    by = {}
    for a in ann["annotations"]:
        by.setdefault(a["image_id"], []).append(a)
    out = []
    for im in ann["images"]:
        f = SRC / "images" / im["file_name"]
        if not f.exists() or not by.get(im["id"]):
            continue
        out.append({
            "path": f, "w": im["width"], "h": im["height"],
            "syms": [{"class": names[a["category_id"]],
                      "box": [a["bbox"][0], a["bbox"][1],
                              a["bbox"][0] + a["bbox"][2],
                              a["bbox"][1] + a["bbox"][3]]}
                     for a in by[im["id"]]]})
    return out


def _font(px):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    return ImageFont.load_default()


def build(name: str, tier: str, cfg: dict, pool: list[dict],
          rng: random.Random) -> dict:
    target = rng.randint(*cfg["n"])
    chosen, total = [], 0
    bag = pool[:]
    rng.shuffle(bag)
    for p in bag:
        if total >= target:
            break
        if total + len(p["syms"]) > target + 4:
            continue
        chosen.append(p)
        total += len(p["syms"])
    if not chosen:                                  # target smaller than any panel
        chosen = [min(pool, key=lambda p: len(p["syms"]))]

    cols = min(cfg["cols"], len(chosen))
    rows = -(-len(chosen) // cols)
    pw = max(p["w"] for p in chosen)
    ph = max(p["h"] for p in chosen)
    pad, margin = 40, 90
    W = margin * 2 + cols * pw + (cols - 1) * pad
    H = margin * 2 + rows * ph + (rows - 1) * pad + 60

    sheet = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(sheet)
    syms = []
    for i, p in enumerate(chosen):
        x = margin + (i % cols) * (pw + pad)
        y = margin + (i // cols) * (ph + pad)
        sheet.paste(Image.open(p["path"]).convert("L"), (x, y))
        d.rectangle([x - 2, y - 2, x + p["w"] + 2, y + p["h"] + 2],
                    outline=0, width=2)
        for s in p["syms"]:
            b = s["box"]
            syms.append({"class": s["class"],
                         "box": [b[0] + x, b[1] + y, b[2] + x, b[3] + y],
                         "panel": p["path"].name})
    d.rectangle([20, 20, W - 20, H - 20], outline=0, width=3)
    d.text((45, H - 55), f"SOVEREIGN WORKBENCH BENCHMARK (REAL PIXELS)   "
                         f"{name}   TIER: {tier.upper()}   "
                         f"{len(syms)} SYMBOLS", fill=0, font=_font(24))
    OUT.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(OUT / f"{name}.png")
    return {"sheet": f"{name}.png", "tier": tier, "width": W, "height": H,
            "panels": [p["path"].name for p in chosen], "symbols": syms}


def main() -> int:
    rng = random.Random(20260916)
    pool = panels()
    print(f"{len(pool)} held-out panels, "
          f"{sum(len(p['syms']) for p in pool)} annotated symbols")
    truth = []
    for tier, cfg in TIERS.items():
        for i in range(cfg["sheets"]):
            t = build(f"{tier}_{i + 1:02d}", tier, cfg, pool, rng)
            truth.append(t)
            print(f"  {t['sheet'][:-4]:14} {t['width']}x{t['height']:<5} "
                  f"{len(t['symbols']):3} symbols  "
                  f"{len(t['panels'])} panels")
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=1))
    print(f"\n{len(truth)} sheets, "
          f"{sum(len(t['symbols']) for t in truth)} symbols -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
