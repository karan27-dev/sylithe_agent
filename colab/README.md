# Training the P&ID detector on Colab

The local run measured ~6 min/epoch on the M1 GPU, so 60 epochs is ~6 hours.
A free Colab T4 does the same work in well under an hour.

## Steps

1. Open a new Colab notebook, **Runtime → Change runtime type → T4 GPU**.
2. Upload `backend/data/pid_dataset_colab.zip` (77 MB).
3. Paste and run:

```python
!pip install -q ultralytics rfdetr onnx onnxruntime
from google.colab import files
files.upload()                       # pick pid_dataset_colab.zip
!unzip -q pid_dataset_colab.zip -d /content/
!nvidia-smi --query-gpu=name,memory.total --format=csv
```

4. Paste `train_pid_detector.py` into a cell and run it.
5. Download the exported `.onnx` back into
   `backend/data/pid_model/` on the workbench.

## What the dataset already contains

Both formats, built from the **same splits** so the comparison is honest:

| Format | Files | Used by |
|---|---|---|
| YOLO | `train/labels/*.txt` + `data.yaml` | ultralytics |
| COCO | `train/_annotations.coco.json` | RF-DETR |

2,399 train / 306 valid / 97 test images, 27 merged classes, every class
present in both train and valid.

## Why these settings

**43% of the boxes are smaller than 32×32 px** at 640 - `ball_valve` is 24×24,
`gate_valve` 31×31. For small objects, input resolution matters more than
architecture:

| Model | Input | A 24 px ball valve becomes |
|---|---|---|
| RF-DETR Nano | 384 | **14 px** — do not use |
| RF-DETR Small | 512 | 19 px |
| RF-DETR Medium | 576 | 22 px |
| RF-DETR Large | 704 | 26 px |
| YOLOv8 @640 | 640 | 24 px |
| **YOLOv8 @1024** | 1024 | **38 px** |

So YOLO trains at 1024, and if you try RF-DETR use **Medium or Large**, never
Nano. Nano and Large are Apache 2.0; XL and 2XL are PML 1.0, not Apache.

**Read the per-class table, not the headline mAP.** The pipeline lives or dies
on whether the small common valves are found, and one number hides that.

## Export ONNX, not .pt

The workbench is an 8 GB M1 with no CUDA. It already runs `onnxruntime` for
OCR and has a CoreML provider, so an ONNX file drops in with no new runtime
and nothing fetched at inference time - which is the point on a sealed machine.

## Known limitation to plan for

Roboflow downsampled the source drawings to 640×640. Real P&IDs are around
7168×4561, where a valve is a handful of pixels. Production will need
**tiling** - slice the drawing into overlapping tiles, detect per tile, merge -
and that will matter more than the choice between YOLO and RF-DETR.
