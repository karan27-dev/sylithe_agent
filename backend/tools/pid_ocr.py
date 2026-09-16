"""
Stage 2 of P&ID understanding: read the tags and attach them to symbols.

Stage 1 (tools/pid_train.py) answers "what is here and where". This answers
"what is it called", which is the join key back to the documents - where the
actual values live. A P&ID carries symbols, tags and topology; it does not
carry set pressures or thicknesses.

Design note. Docling reports 14 characters ("<!-- image -->") for a P&ID
because its layout model calls the whole drawing one picture and never invokes
OCR. Calling RapidOCR directly on the page reads it fine - measured on
PID-CDU2-004.png: 12 text boxes including TK-4102, PSV-2041, LT-4102, HV-4021,
FV-4033 and P-4110A. So we OCR the whole page ONCE and associate text to
symbols by position, rather than cropping per detection and paying for N OCR
passes.

    python -m tools.pid_ocr data/corpus/PID-CDU2-004.png
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OCR_MODELS = Path.home() / ".cache" / "docling" / "models" / "RapidOcr"
# Trained on Colab (T4, 60 epochs @1024): mAP50 0.890, mAP50-95 0.600.
# ONNX is preferred here - this machine has no CUDA but already runs
# onnxruntime with a CoreML provider, so there is no new runtime to install
# and nothing is fetched at inference time.
WEIGHTS_ONNX = _ROOT / "data" / "pid_model" / "best.onnx"
WEIGHTS_PT = _ROOT / "data" / "pid_model" / "best.pt"
WEIGHTS = WEIGHTS_ONNX if WEIGHTS_ONNX.exists() else WEIGHTS_PT

# The detector was trained at 1024. Running it at a smaller size shrinks a
# 24 px ball valve back below what it learned to see.
IMGSZ = 1024

# Sheets wider (or taller) than this are cut into overlapping tiles instead of
# being scaled down in one pass - see detect() for the measured difference.
# 2048 is deliberately above any drawing that already fits comfortably, so
# small test images keep the fast single-pass path.
TILE_ABOVE = 2048
TILE_PX = 1024          # matches IMGSZ: each tile runs at native resolution
TILE_OVERLAP = 0.2      # a symbol on a tile seam still lands whole in one tile
MAX_DET = 1000          # ultralytics defaults to 300; a dense sheet exceeds it
OCR_TILE_PX = 1280      # text needs more context than a symbol does

# ISA-style equipment and instrument tags: letters, then a loop number,
# optionally a suffix letter for duplicates (P-4110A vs P-4110B).
TAG = re.compile(r"\b([A-Z]{1,4})\s?[-–]\s?(\d{2,5})([A-Z])?\b")

# The top line of an instrument bubble is the function code on its own - "LT"
# above, "4102" below. Kept separate so a bare code can be paired with a
# nearby number rather than thrown away.
FUNC = re.compile(r"^[A-Z]{2,4}$")

# How far a tag may sit from a symbol and still belong to it, as a multiple of
# the symbol's own size. Tags are drawn beside or above the glyph, not on it.
MAX_DIST_FACTOR = 3.0


@dataclass
class TextBox:
    text: str
    cx: float
    cy: float
    score: float

    @property
    def is_tag(self) -> bool:
        return bool(TAG.search(self.text))


@dataclass
class Symbol:
    cls: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float
    tag: str | None = None
    tag_conf: float = 0.0
    nearby_text: list[str] = field(default_factory=list)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def size(self) -> float:
        return max(self.x2 - self.x1, self.y2 - self.y1)

    def as_dict(self) -> dict:
        return {"class": self.cls, "tag": self.tag, "conf": round(self.conf, 3),
                "box": [round(v) for v in (self.x1, self.y1, self.x2, self.y2)]}


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

_ocr = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR(
            det_model_path=str(OCR_MODELS / "PP-OCRv6_det_small.onnx"),
            cls_model_path=str(OCR_MODELS / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
            rec_model_path=str(OCR_MODELS / "PP-OCRv6_rec_small.onnx"),
        )
    return _ocr


def _ocr_boxes(src, min_score: float, dx: float = 0.0,
               dy: float = 0.0) -> list[TextBox]:
    res, _ = _engine()(src)
    out = []
    for box, txt, score in (res or []):
        if score < min_score or not txt.strip():
            continue
        out.append(TextBox(
            text=txt.strip(),
            cx=sum(p[0] for p in box) / 4 + dx,
            cy=sum(p[1] for p in box) / 4 + dy,
            score=float(score)))
    return out


def read_text(image: str | Path, min_score: float = 0.5,
              tile: bool | None = None) -> list[TextBox]:
    """Every text box on the page, with its centre.

    The detector was tiled first and the tags were left on a single pass, which
    hid a second copy of the same bug. RapidOCR resizes internally to a few
    hundred pixels on the long side, so on a 6400x4300 sheet a tag is a smear:
    measured, the same reader returns 'TB-101', 'GV-102' on a 1600 px sheet and
    'ere', '3 102', 'ze' on a 6400 px one - 0% of tags recovered. Symbols were
    being found and then had no name, which makes an isolation answer
    impossible however good detection is.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(str(image)).convert("RGB")
    w, h = img.size
    if tile is None:
        tile = max(w, h) > TILE_ABOVE
    if not tile:
        return _ocr_boxes(str(image), min_score)

    import numpy as np

    found: list[TextBox] = []
    for x, y in _tiles(w, h, OCR_TILE_PX, TILE_OVERLAP):
        crop = img.crop((x, y, min(x + OCR_TILE_PX, w), min(y + OCR_TILE_PX, h)))
        found += _ocr_boxes(np.asarray(crop), min_score, x, y)

    # The same tag read twice in an overlap is one tag, not two.
    kept: list[TextBox] = []
    for t in sorted(found, key=lambda z: -z.score):
        if not any(o.text == t.text and abs(o.cx - t.cx) < OCR_TILE_PX * 0.1
                   and abs(o.cy - t.cy) < OCR_TILE_PX * 0.1 for o in kept):
            kept.append(t)
    return kept


def _read_text_unused(image: str | Path, min_score: float = 0.5) -> list[TextBox]:
    res, _ = _engine()(str(image))
    out = []
    for box, txt, score in (res or []):
        if score < min_score or not txt.strip():
            continue
        out.append(TextBox(
            text=txt.strip(),
            cx=sum(p[0] for p in box) / 4,
            cy=sum(p[1] for p in box) / 4,
            score=float(score),
        ))
    return out


def pair_bubbles(boxes: list[TextBox], max_gap: float = 90.0) -> list[TextBox]:
    """
    Rebuild split instrument tags.

    An instrument bubble is drawn as two stacked lines - "LT" over "4102" -
    and OCR returns them as separate boxes. Where a bare function code sits
    directly above a bare number, emit the joined tag as well.
    """
    extra: list[TextBox] = []
    funcs = [b for b in boxes if FUNC.match(b.text)]
    nums = [b for b in boxes if b.text.isdigit() and 2 <= len(b.text) <= 5]
    for f in funcs:
        best, bd = None, max_gap
        for n in nums:
            if n.cy <= f.cy:                      # number must be BELOW the code
                continue
            d = math.hypot(n.cx - f.cx, n.cy - f.cy)
            if d < bd:
                best, bd = n, d
        if best is not None:
            extra.append(TextBox(f"{f.text}-{best.text}", f.cx,
                                 (f.cy + best.cy) / 2,
                                 min(f.score, best.score)))
    return boxes + extra


def tags_only(boxes: list[TextBox]) -> list[TextBox]:
    seen: set[str] = set()
    out = []
    for b in boxes:
        m = TAG.search(b.text)
        if not m:
            continue
        norm = f"{m.group(1)}-{m.group(2)}{m.group(3) or ''}"
        if norm in seen:
            continue
        seen.add(norm)
        out.append(TextBox(norm, b.cx, b.cy, b.score))
    return out


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def _iou(a: Symbol, b: Symbol) -> float:
    x1, y1 = max(a.x1, b.x1), max(a.y1, b.y1)
    x2, y2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    ar = lambda s: (s.x2 - s.x1) * (s.y2 - s.y1)
    return inter / (ar(a) + ar(b) - inter)


def _dedupe(syms: list[Symbol], thr: float = 0.5) -> list[Symbol]:
    """Overlapping tiles see the same symbol twice. Keep the confident copy."""
    kept: list[Symbol] = []
    for s in sorted(syms, key=lambda z: -z.conf):
        if not any(_iou(s, k) > thr for k in kept):
            kept.append(s)
    return kept


def _tiles(w: int, h: int, tile: int, overlap: float):
    step = max(1, int(tile * (1 - overlap)))
    ys = list(range(0, max(h - tile, 0) + step, step)) or [0]
    xs = list(range(0, max(w - tile, 0) + step, step)) or [0]
    for y in ys:
        for x in xs:
            yield min(x, max(w - tile, 0)), min(y, max(h - tile, 0))


def detect(image: str | Path, weights: Path = WEIGHTS,
           conf: float = 0.25, tile: bool | None = None) -> list[Symbol]:
    """Stage 1. Returns [] with a clear message if the model is not trained.

    A real plant P&ID is around 7168x4562. Handing that to a detector trained
    at 1024 means the whole sheet is squeezed down by 7x, so a 40 px valve
    arrives as 6 px and there is nothing left to recognise. Measured on 20
    Digitize-PID val sheets (2497 symbols, class-agnostic, IoU 0.5):

        whole sheet, one pass    recall 14.5%   precision 73.6%
        overlapping tiles        recall 26.0%   precision 81.1%

    Note precision goes UP. Tiling is not trading accuracy for recall - the
    detector was never wrong, it was being shown a sheet it could not see.
    The cost is time: one pass is ~0.5s, tiling ~23s on this machine.

    Small drawings still take the single pass - they already fit, and tiling
    them would only add latency.
    """
    if not Path(weights).exists():
        return []
    import os
    os.environ.setdefault("YOLO_OFFLINE", "1")
    from PIL import Image
    from ultralytics import YOLO

    Image.MAX_IMAGE_PIXELS = None          # plant sheets trip the bomb guard
    img = Image.open(str(image)).convert("RGB")
    w, h = img.size
    if tile is None:
        tile = max(w, h) > TILE_ABOVE

    model = YOLO(str(weights), task="detect")

    def run(src, dx: int = 0, dy: int = 0) -> list[Symbol]:
        out = []
        for r in model.predict(src, imgsz=IMGSZ, conf=conf,
                               verbose=False, max_det=MAX_DET):
            for b in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                out.append(Symbol(r.names[int(b.cls)], float(b.conf),
                                  x1 + dx, y1 + dy, x2 + dx, y2 + dy))
        return out

    if not tile:
        return run(str(image))

    found: list[Symbol] = []
    for x, y in _tiles(w, h, TILE_PX, TILE_OVERLAP):
        crop = img.crop((x, y, min(x + TILE_PX, w), min(y + TILE_PX, h)))
        found += run(crop, x, y)
    return _dedupe(found)


# ---------------------------------------------------------------------------
# association
# ---------------------------------------------------------------------------

def associate(symbols: list[Symbol], tags: list[TextBox]) -> list[Symbol]:
    """
    Give each symbol its nearest unclaimed tag.

    Nearest-first across all pairs rather than per-symbol, so a tag sitting
    between two glyphs goes to the one it is actually closest to instead of
    whichever symbol happened to be processed first.
    """
    pairs = []
    for si, s in enumerate(symbols):
        for ti, t in enumerate(tags):
            d = math.hypot(t.cx - s.cx, t.cy - s.cy)
            if d <= s.size * MAX_DIST_FACTOR:
                pairs.append((d, si, ti))
    pairs.sort()

    used_s: set[int] = set()
    used_t: set[int] = set()
    for d, si, ti in pairs:
        if si in used_s or ti in used_t:
            continue
        symbols[si].tag = tags[ti].text
        symbols[si].tag_conf = tags[ti].score
        used_s.add(si)
        used_t.add(ti)
    return symbols


def analyze(image: str | Path, weights: Path = WEIGHTS,
            conf: float = 0.25) -> dict:
    """Stage 1 + 2 together. Works OCR-only while the detector is training."""
    boxes = pair_bubbles(read_text(image))
    tags = tags_only(boxes)
    tags, corrections = correct_tags(tags)
    symbols = associate(detect(image, weights, conf), tags)

    claimed = {s.tag for s in symbols if s.tag}
    return {
        "image": str(image),
        "symbols": [s.as_dict() for s in symbols],
        "tags": [t.text for t in tags],
        # positions too: a caller that re-runs OCR sees the UNcorrected text
        # and cannot match it back ("PSV-241" != "PSV-2041"), which silently
        # dropped the relief valve from the graph.
        "tag_boxes": [{"text": t.text, "cx": t.cx, "cy": t.cy,
                       "score": t.score} for t in tags],
        "unmatched_tags": [t.text for t in tags if t.text not in claimed],
        "text_boxes": len(boxes),
        "corrections": corrections,
        "detector": "trained" if Path(weights).exists() else "not trained yet",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--conf", type=float, default=0.25)
    a = ap.parse_args()

    r = analyze(a.image, conf=a.conf)
    print(f"{Path(a.image).name}")
    print(f"  detector : {r['detector']}")
    print(f"  text     : {r['text_boxes']} boxes")
    print(f"  tags     : {len(r['tags'])}  {r['tags']}")
    for was, now in r.get("corrections", []):
        print(f"  corrected: {was}  ->  {now}   (matched a tag in the documents)")
    if r["symbols"]:
        print(f"\n  {'symbol':22} {'tag':12} conf")
        for s in r["symbols"]:
            print(f"   {s['class']:22} {str(s['tag'] or '-'):12} {s['conf']:.2f}")
    if r["unmatched_tags"]:
        print(f"\n  tags with no symbol: {r['unmatched_tags']}")
    return 0




# ---------------------------------------------------------------------------
# tag correction against the document index
# ---------------------------------------------------------------------------
# OCR on a drawing drops characters: measured on PID-CDU2-004.png it read
# "PSV-241" where the drawing says "PSV-2041". A wrong tag is worse than no
# tag, because the whole point of a tag is to be the join key into the
# documents - and PSV-241 joins to nothing.
#
# We already have the answer lying around. The indexed corpus is full of real
# tags, so treat it as the vocabulary and snap a near-miss onto the real thing.

_KNOWN: set[str] | None = None


def known_tags(client=None) -> set[str]:
    """Every tag that appears in the indexed documents."""
    global _KNOWN
    if _KNOWN is not None:
        return _KNOWN
    tags: set[str] = set()
    try:
        from ingest import pipeline
        db = pipeline._db()
        if pipeline.TABLE in pipeline._tables(db):
            for row in db.open_table(pipeline.TABLE).search().limit(100000).to_list():
                for m in TAG.finditer(row.get("text", "").upper()):
                    tags.add(f"{m.group(1)}-{m.group(2)}{m.group(3) or ''}")
    except Exception:
        pass
    _KNOWN = tags
    return tags


def _close(a: str, b: str) -> bool:
    """
    One edit apart, where the edit is a dropped or added digit.

    Deliberately narrow. Snapping PSV-241 to PSV-2041 is a fix; snapping
    P-4110A to P-4110B would invent a different pump.
    """
    pa, pb = a.split("-"), b.split("-")
    if len(pa) != 2 or len(pb) != 2 or pa[0] != pb[0]:
        return False            # never change the letters
    x, y = pa[1], pb[1]
    if abs(len(x) - len(y)) != 1:
        return False            # exactly one character different in length
    short, long_ = (x, y) if len(x) < len(y) else (y, x)
    for i in range(len(long_)):          # is short == long with one char removed
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def correct_tags(tags: list[TextBox], vocab: set[str] | None = None
                 ) -> tuple[list[TextBox], list[tuple[str, str]]]:
    """Snap OCR near-misses onto tags that actually exist in the documents."""
    vocab = known_tags() if vocab is None else vocab
    if not vocab:
        return tags, []
    fixed, changes = [], []
    for t in tags:
        if t.text in vocab:
            fixed.append(t)
            continue
        match = next((v for v in vocab if _close(t.text, v)), None)
        if match:
            changes.append((t.text, match))
            fixed.append(TextBox(match, t.cx, t.cy, t.score))
        else:
            fixed.append(t)
    return fixed, changes


if __name__ == "__main__":
    raise SystemExit(main())
