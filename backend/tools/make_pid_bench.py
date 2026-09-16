"""
Build a real P&ID benchmark: 20 drawings with exact ground truth, four
difficulty tiers, plus the documents that carry the numbers.

WHY NOT DRAW THE SYMBOLS MYSELF
    A hand-drawn glyph is not what the detector was trained on, so a benchmark
    built from my own shapes would measure drawing style, not detection. Every
    symbol here is a real instance CROPPED FROM THE HELD-OUT TEST SPLIT - the
    97 sheets the model never trained on. The symbols are therefore in
    distribution and previously unseen, while the sheets themselves are new.

WHY THE SHEETS GROW WITH DIFFICULTY
    A real plant P&ID is around 7168x4562 with symbols about 60 px across. If
    every tier were drawn on a small canvas the benchmark would quietly test
    the easy case only, which is exactly how "22 of 24" hid a pipeline that
    collapses on a full-size sheet.

Ground truth per sheet: every symbol's class, box and tag, and every pipe
connection. Documents hold the values, because a P&ID never states a set
pressure - it states a tag.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
SRC = _ROOT / "data" / "pid_yolo" / "test"          # held out from training
OUT = _ROOT / "data" / "bench_pid"

# symbols per sheet, and the canvas they are drawn on. The symbol size stays
# roughly constant in pixels, so a harder tier means a bigger sheet - the same
# relationship a real drawing has.
TIERS = {
    "easy":      dict(n=(6, 9),   size=(1600, 1100), sym=70, sheets=5),
    "hard":      dict(n=(12, 20), size=(2600, 1800), sym=70, sheets=5),
    "very_hard": dict(n=(22, 30), size=(4200, 2900), sym=68, sheets=5),
    "complex":   dict(n=(34, 48), size=(6400, 4300), sym=64, sheets=5),
}

EQUIPMENT = ["vessel", "pump", "tower", "reactor", "compressor",
             "fired_heater", "turbine", "fan", "conveyor"]
INLINE = ["gate_valve", "globe_valve", "ball_valve", "butterfly_valve",
          "check_valve", "control_valve", "plug_valve", "needle_valve",
          "diaphragm_valve", "angle_valve", "relief_valve", "pipe_fitting"]
INSTRUMENT = ["pressure_instrument", "flow_instrument", "level_instrument",
              "temp_instrument"]

# tag prefixes, kept consistent with pid_graph.PREFIX_CLASS so an OCR-only
# node still resolves to the right kind of thing
PREFIX = {
    "vessel": "TK", "pump": "P", "tower": "T", "reactor": "R",
    "compressor": "K", "fired_heater": "F", "turbine": "TB", "fan": "FN",
    "conveyor": "CN",
    "gate_valve": "HV", "globe_valve": "GV", "ball_valve": "BV",
    "butterfly_valve": "BF", "check_valve": "CV", "control_valve": "FV",
    "plug_valve": "PLV", "needle_valve": "NV", "diaphragm_valve": "DV",
    "angle_valve": "AV", "relief_valve": "PSV", "pipe_fitting": "FIT",
    "pressure_instrument": "PT", "flow_instrument": "FT",
    "level_instrument": "LT", "temp_instrument": "TT",
}


def _font(px: int):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    return ImageFont.load_default()


def symbol_bank() -> dict[str, list[Image.Image]]:
    """Crop every annotated instance out of the held-out test sheets."""
    ann = json.loads((SRC / "_annotations.coco.json").read_text())
    names = {c["id"]: c["name"] for c in ann["categories"]}
    by_img = defaultdict(list)
    for a in ann["annotations"]:
        by_img[a["image_id"]].append(a)

    bank: dict[str, list[Image.Image]] = defaultdict(list)
    for im in ann["images"]:
        f = SRC / "images" / im["file_name"]
        if not f.exists():
            continue
        page = Image.open(f).convert("L")
        for a in by_img[im["id"]]:
            x, y, w, h = a["bbox"]
            if w < 18 or h < 18 or w > 260 or h > 260:
                continue                      # unusable crops, either way
            pad = 3
            crop = page.crop((max(0, x - pad), max(0, y - pad),
                              min(page.width, x + w + pad),
                              min(page.height, y + h + pad)))
            # Source sheets are scans, so ink is grey and speckled. Pasted as
            # is, a symbol came out visibly fainter than the pipe drawn next to
            # it - the benchmark would then be measuring JPEG grey, not shape.
            # Threshold to solid ink, the way a real CAD sheet prints.
            crop = crop.point(lambda v: 0 if v < 165 else 255)
            bank[names[a["category_id"]]].append(crop)
    return {k: v for k, v in bank.items() if v}


class Sheet:
    def __init__(self, w: int, h: int, sym: int, rng: random.Random):
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)
        self.sym, self.rng = sym, rng
        self.f = _font(max(13, sym // 4))
        self.syms: list[dict] = []
        self.edges: list[tuple[str, str]] = []

    def place(self, glyph: Image.Image, cls: str, tag: str,
              cx: int, cy: int) -> None:
        g = glyph.resize((self.sym, self.sym), Image.LANCZOS)
        g = g.point(lambda v: 0 if v < 165 else 255)      # resize re-greys it
        x, y = cx - self.sym // 2, cy - self.sym // 2
        # paste ink only, so the pipe already drawn shows through the gaps
        self.img.paste(g, (x, y), g.point(lambda v: 255 if v < 128 else 0))
        self.d.text((cx - self.sym // 2, y + self.sym + 4), tag,
                    fill=0, font=self.f)
        self.syms.append({"class": cls, "tag": tag,
                          "box": [x, y, x + self.sym, y + self.sym]})

    def pipe(self, a: tuple[int, int], b: tuple[int, int], width: int = 3):
        self.d.line([a, b], fill=0, width=width)

    def signal(self, a: tuple[int, int], b: tuple[int, int]):
        """Instrument signal: dashed, per ISA 5.1."""
        (x1, y1), (x2, y2) = a, b
        n = max(2, int(abs(y2 - y1) / 16))
        for i in range(n):
            if i % 2:
                continue
            t0, t1 = i / n, (i + 0.6) / n
            self.d.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0),
                         (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)],
                        fill=0, width=2)


def build_sheet(name: str, tier: str, cfg: dict, bank: dict,
                rng: random.Random) -> dict:
    W, H = cfg["size"]
    target = rng.randint(*cfg["n"])
    s = Sheet(W, H, cfg["sym"], rng)
    s.d.rectangle([30, 30, W - 30, H - 30], outline=0, width=3)
    s.d.text((50, H - 70), f"SOVEREIGN WORKBENCH BENCHMARK   {name}   TIER: "
                           f"{tier.upper()}", fill=0, font=_font(26))

    # Lay the items out on a grid shaped like the sheet, so a drawing is used
    # edge to edge. Sizing the grid from the sheet height instead left the
    # bottom third of every complex sheet blank - which would have made the
    # hardest tier the easiest one to search.
    margin = 150
    per_row = max(3, round((target * (W - 2 * margin) /
                            max(1, H - 2 * margin)) ** 0.5))
    rows = max(1, -(-target // per_row))
    loop, placed = 101, 0
    prev: str | None = None

    for r in range(rows):
        if placed >= target:
            break
        y = int(margin + (r + 0.5) * (H - 2 * margin) / rows)
        xs = [int(margin + (i + 0.5) * (W - 2 * margin) / per_row)
              for i in range(per_row)]
        s.pipe((xs[0], y), (xs[-1], y))
        if r:                                  # serpentine riser to the row above
            x_end = xs[-1] if r % 2 else xs[0]
            s.pipe((x_end, y), (x_end, y - (H - 2 * margin) // rows))
        for x in xs:
            if placed >= target:
                break
            pool = EQUIPMENT if placed % 3 == 0 else INLINE
            pool = [c for c in pool if c in bank] or list(bank)
            cls = rng.choice(pool)
            tag = f"{PREFIX.get(cls, 'XX')}-{loop}"
            loop += 1
            s.place(rng.choice(bank[cls]), cls, tag, x, y)
            if prev:
                s.edges.append((prev, tag))
            prev = tag
            placed += 1

            # an instrument above some equipment, joined by a dashed signal
            if cls in EQUIPMENT and rng.random() < 0.5 and placed < target:
                icls = rng.choice([c for c in INSTRUMENT if c in bank] or [cls])
                itag = f"{PREFIX.get(icls, 'XI')}-{loop}"
                loop += 1
                iy = y - int(cfg["sym"] * 1.9)
                s.signal((x, y - cfg["sym"] // 2), (x, iy + cfg["sym"] // 2))
                s.place(rng.choice(bank[icls]), icls, itag, x, iy)
                s.edges.append((tag, itag))
                placed += 1

    OUT.mkdir(parents=True, exist_ok=True)
    s.img.convert("RGB").save(OUT / f"{name}.png")
    return {"sheet": f"{name}.png", "tier": tier, "width": W, "height": H,
            "symbols": s.syms, "edges": s.edges}


def documents(truth: list[dict]) -> None:
    """The numbers a drawing never carries. One row per tagged item."""
    rng = random.Random(7)
    rows, md = [], ["# Benchmark plant records",
                    "",
                    "Generated alongside the benchmark drawings. A P&ID states "
                    "a tag; these state the value behind it.", ""]
    for t in truth:
        md.append(f"\n## {t['sheet']}  ({t['tier']})\n")
        md.append("| tag | type | design pressure (barg) | "
                  "set pressure (barg) | measured thickness (mm) | last "
                  "inspection |")
        md.append("|---|---|---|---|---|---|")
        for sy in t["symbols"]:
            dp = round(rng.uniform(6, 40), 1)
            sp = round(dp * rng.uniform(0.85, 0.98), 1) \
                if sy["class"] == "relief_valve" else ""
            th = round(rng.uniform(6.0, 18.0), 1)
            dt = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"
            rows.append([sy["tag"], sy["class"], dp, sp, th, dt, t["sheet"]])
            md.append(f"| {sy['tag']} | {sy['class']} | {dp} | {sp} | "
                      f"{th} | {dt} |")
    (OUT / "records.md").write_text("\n".join(md))
    with (OUT / "records.csv").open("w") as fh:
        fh.write("tag,type,design_barg,set_barg,thickness_mm,inspected,sheet\n")
        for r in rows:
            fh.write(",".join(str(v) for v in r) + "\n")


def main() -> int:
    rng = random.Random(20260916)
    bank = symbol_bank()
    print(f"symbol bank: {sum(len(v) for v in bank.values())} crops "
          f"across {len(bank)} classes (held-out test split)")
    truth = []
    for tier, cfg in TIERS.items():
        for i in range(cfg["sheets"]):
            name = f"{tier}_{i + 1:02d}"
            t = build_sheet(name, tier, cfg, bank, rng)
            truth.append(t)
            print(f"  {name:16} {t['width']}x{t['height']:<6} "
                  f"{len(t['symbols']):3} symbols  {len(t['edges']):3} edges")
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=1))
    documents(truth)
    n = sum(len(t["symbols"]) for t in truth)
    print(f"\n{len(truth)} sheets, {n} symbols, "
          f"{sum(len(t['edges']) for t in truth)} connections -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
