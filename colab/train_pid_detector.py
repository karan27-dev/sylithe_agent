"""
Colab: train and compare P&ID symbol detectors, then export for offline use.

Run this on a GPU runtime (Runtime -> Change runtime type -> T4). It trains
YOLOv8s at 1024 and RF-DETR Medium on the SAME splits, compares them per class,
and exports the winner to ONNX so it runs on the air-gapped machine through
onnxruntime - no CUDA, no PyTorch-on-MPS surprises.

Why these two and these settings, in short:

  * 43% of our boxes are under 32x32 px at 640 - ball_valve sits at 24x24 and
    gate_valve at 31x31. This is a small-object problem, and for small objects
    input resolution matters more than architecture. YOLO trains at 1024 so a
    24 px valve becomes 38 px.
  * RF-DETR Nano runs at 384x384, which would shrink that same valve to 14 px.
    Do not use Nano here. Medium (576) is the smallest sensible option and is
    Apache 2.0; Large (704) is better if the runtime holds it.
  * Compare per class, not just overall mAP. The question is whether the model
    finds the SMALL common valves, and a headline mAP hides that.

Upload backend/data/pid_dataset_colab.zip when prompted.
"""

# ---------------------------------------------------------------------------
# 1. setup
# ---------------------------------------------------------------------------
# !pip install -q ultralytics rfdetr onnx onnxruntime
#
# from google.colab import files
# up = files.upload()                 # pid_dataset_colab.zip  (~77 MB)
# !unzip -q pid_dataset_colab.zip -d /content/
#
# !nvidia-smi --query-gpu=name,memory.total --format=csv

import json
import time
from pathlib import Path

DATA = Path("/content/pid_yolo")
YAML = DATA / "data.yaml"
OUT = Path("/content/out")
OUT.mkdir(exist_ok=True)

# Small objects need resolution. 1024 is the largest that trains comfortably
# on a T4 at batch 8; drop to 800 if you hit out-of-memory.
YOLO_IMGSZ = 1024
YOLO_BATCH = 8
EPOCHS = 60


# ---------------------------------------------------------------------------
# 2. YOLOv8s at high resolution
# ---------------------------------------------------------------------------

def train_yolo():
    from ultralytics import YOLO
    m = YOLO("yolov8s.pt")           # 's' not 'n': we have a GPU now
    t = time.time()
    m.train(data=str(YAML), epochs=EPOCHS, imgsz=YOLO_IMGSZ, batch=YOLO_BATCH,
            device=0, workers=2, patience=15, seed=7,
            project=str(OUT), name="yolo", exist_ok=True, plots=True)
    mins = (time.time() - t) / 60
    r = m.val(data=str(YAML), imgsz=YOLO_IMGSZ, split="val")
    per_class = {r.names[c]: float(r.box.ap50[i])
                 for i, c in enumerate(r.ap_class_index)}
    return {"name": f"yolov8s@{YOLO_IMGSZ}", "map50": float(r.box.map50),
            "map": float(r.box.map), "minutes": round(mins, 1),
            "per_class": per_class,
            "weights": str(OUT / "yolo" / "weights" / "best.pt")}


# ---------------------------------------------------------------------------
# 3. RF-DETR Medium  (COCO format, same splits)
# ---------------------------------------------------------------------------

def train_rfdetr():
    from rfdetr import RFDETRMedium
    m = RFDETRMedium()
    t = time.time()
    # rfdetr expects train/ valid/ test/ each holding images plus
    # _annotations.coco.json - which tools/pid_dataset.py already wrote.
    m.train(dataset_dir=str(DATA), epochs=EPOCHS, batch_size=4,
            grad_accum_steps=4, lr=1e-4, output_dir=str(OUT / "rfdetr"))
    mins = (time.time() - t) / 60
    return {"name": "rf-detr-medium@576", "minutes": round(mins, 1),
            "weights": str(OUT / "rfdetr" / "checkpoint_best_total.pth")}


# ---------------------------------------------------------------------------
# 4. compare where it matters
# ---------------------------------------------------------------------------

# The classes the whole pipeline depends on. relief_valve is safety critical;
# the rest are the small, common ones that decide whether a graph can be built.
CRITICAL = ["relief_valve", "gate_valve", "ball_valve", "globe_valve",
            "check_valve", "needle_valve", "butterfly_valve", "control_valve"]


def compare(a: dict, b: dict | None = None):
    print(f"\n{'model':24} {'mAP50':>8} {'mAP50-95':>10} {'minutes':>9}")
    for r in (x for x in (a, b) if x):
        print(f"  {r['name']:22} {r.get('map50', 0):8.3f} "
              f"{r.get('map', 0):10.3f} {r['minutes']:9.1f}")

    if a.get("per_class"):
        print(f"\n{'critical class':22} {'AP50':>8}")
        for c in CRITICAL:
            v = a["per_class"].get(c)
            print(f"  {c:20} {v:8.3f}" if v is not None else f"  {c:20}    absent")
        worst = sorted(a["per_class"].items(), key=lambda kv: kv[1])[:5]
        print("\nweakest classes overall:")
        for n, v in worst:
            print(f"  {n:20} {v:8.3f}")


# ---------------------------------------------------------------------------
# 5. export for the air-gapped machine
# ---------------------------------------------------------------------------

def export_onnx(weights: str, imgsz: int = YOLO_IMGSZ) -> str:
    """
    ONNX, not .pt.

    The workbench machine is an 8 GB M1 with no CUDA. It already runs
    onnxruntime for OCR and has a CoreML provider available, so an ONNX file
    drops straight in with no new runtime and nothing to download at inference.
    """
    from ultralytics import YOLO
    p = YOLO(weights).export(format="onnx", imgsz=imgsz, opset=12, simplify=True)
    print("exported:", p)
    return str(p)


if __name__ == "__main__":
    y = train_yolo()
    print(json.dumps({k: v for k, v in y.items() if k != "per_class"}, indent=2))

    r = None
    try:
        r = train_rfdetr()
    except Exception as exc:
        print(f"\nRF-DETR skipped: {type(exc).__name__}: {exc}")

    compare(y, r)
    onnx = export_onnx(y["weights"])
    (OUT / "results.json").write_text(json.dumps({"yolo": y, "rfdetr": r}, indent=2))
    print("\nDownload these back to the workbench:")
    print(f"  {onnx}")
    print(f"  {y['weights']}")
    # from google.colab import files; files.download(onnx)
