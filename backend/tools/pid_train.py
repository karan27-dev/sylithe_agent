"""
Train the P&ID symbol detector.

Stage 1 of P&ID understanding: given a drawing, say WHAT is there and WHERE.
It does not read tags (that is OCR on the crop) and it does not know values
(those live in the documents, joined by tag).

Runs on the M1 GPU through MPS. Weights land in data/pid_model/ and are used
offline afterwards - training needs the dataset on disk, nothing else.

    python -m tools.pid_train                 # train
    python -m tools.pid_train --eval          # evaluate the trained weights
    python -m tools.pid_train --predict FILE  # run on one drawing
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Ultralytics phones home for analytics and version checks unless told not to.
# On a sovereignty project that must be off before the import.
os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("ULTRALYTICS_OFFLINE", "1")

_ROOT = Path(__file__).resolve().parent.parent
DATA = _ROOT / "data" / "pid_yolo" / "data.yaml"
RUNS = _ROOT / "data" / "pid_model"
BEST = RUNS / "train" / "weights" / "best.pt"


def _settings() -> None:
    """Disable ultralytics telemetry and pin its runs directory."""
    from ultralytics import settings
    settings.update({"sync": False, "runs_dir": str(RUNS),
                     "datasets_dir": str(_ROOT / "data")})


def train(epochs: int = 60, imgsz: int = 640, batch: int = 8,
          model: str = "yolov8n.pt") -> Path:
    _settings()
    from ultralytics import YOLO

    m = YOLO(model)
    m.train(
        data=str(DATA), epochs=epochs, imgsz=imgsz, batch=batch,
        device="mps",          # Apple GPU
        workers=2,             # 8 GB machine; more workers just thrash
        cache=False,           # 2400 images at 640px will not fit comfortably
        patience=15,           # stop early if validation stops improving
        project=str(RUNS), name="train", exist_ok=True,
        seed=7, plots=True, val=True, verbose=True,
    )
    return BEST


def evaluate(weights: Path = BEST) -> None:
    _settings()
    from ultralytics import YOLO
    r = YOLO(str(weights)).val(data=str(DATA), device="mps", split="valid")
    print(f"\nmAP50    {r.box.map50:.3f}")
    print(f"mAP50-95 {r.box.map:.3f}")
    names = r.names
    print(f"\n{'class':22} {'AP50':>7} {'P':>7} {'R':>7}")
    order = sorted(range(len(r.box.ap50)), key=lambda i: -r.box.ap50[i])
    for i in order:
        ci = r.ap_class_index[i]
        print(f"  {names[ci]:20} {r.box.ap50[i]:7.3f} "
              f"{r.box.p[i]:7.3f} {r.box.r[i]:7.3f}")


def predict(path: str, weights: Path = BEST, conf: float = 0.25) -> None:
    _settings()
    from ultralytics import YOLO
    import collections
    res = YOLO(str(weights)).predict(path, device="mps", conf=conf, verbose=False)
    for r in res:
        c = collections.Counter(r.names[int(b.cls)] for b in r.boxes)
        print(f"{Path(path).name}: {len(r.boxes)} symbols")
        for n, k in c.most_common():
            print(f"   {k:4}  {n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--predict")
    a = ap.parse_args()

    if a.eval:
        evaluate()
    elif a.predict:
        predict(a.predict)
    else:
        w = train(epochs=a.epochs, imgsz=a.imgsz, batch=a.batch)
        print(f"\nweights: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
