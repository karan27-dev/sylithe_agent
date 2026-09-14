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
WEIGHTS = _ROOT / "data" / "pid_model" / "train" / "weights" / "best.pt"

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


def read_text(image: str | Path, min_score: float = 0.5) -> list[TextBox]:
    """Every text box on the page, with its centre. One OCR pass."""
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

def detect(image: str | Path, weights: Path = WEIGHTS,
           conf: float = 0.25) -> list[Symbol]:
    """Stage 1. Returns [] with a clear message if the model is not trained."""
    if not Path(weights).exists():
        return []
    import os
    os.environ.setdefault("YOLO_OFFLINE", "1")
    from ultralytics import YOLO

    res = YOLO(str(weights)).predict(str(image), conf=conf, verbose=False)
    out = []
    for r in res:
        for b in r.boxes:
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            out.append(Symbol(r.names[int(b.cls)], float(b.conf), x1, y1, x2, y2))
    return out


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
