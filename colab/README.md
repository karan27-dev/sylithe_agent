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

## Results — what the trained detector actually measures

Everything below is measured on real held-out P&ID sheets, not the Roboflow
validation split. `81.7%` is the number that matters end to end; the rest is
where it comes from and what tiling bought.

| stage | result |
|---|---|
| Symbol detection, 496 annotated symbols | **81.7%** located, **76.4%** correct class, F1 **0.770** |
| Same detector, tiling turned off (the control) | 37.3% located, F1 0.503 |
| Tag reading (RapidOCR), 433 symbols | **86.8%** |
| Connectivity (line tracing → graph) | F1 **0.179** — the weakest stage, and no model upgrade fixes it; it needs better line tracing |

**Tiling is what production needed, not a bigger model.** A detector trained
at 640–1024 px sees a full 7168×4562 sheet squeezed 7–11×, so a 24 px valve
arrives at the model as a handful of pixels. Cutting the sheet into
overlapping tiles and merging detections back fixed it — measured across 20
sheets, 496 symbols, split by how crowded the sheet is:

| symbols on the sheet | one pass (no tiling) | tiled |
|---|---|---|
| 6–9 | 79.6% | **91.8%** |
| 12–20 | 39.5% | **77.9%** |
| 22–30 | 51.8% | **83.0%** |
| 34–48 | **17.7%** | **80.0%** |

Read the left column downward: a single forward pass degrades hard as a sheet
gets busier. Tiling holds roughly flat regardless of symbol count — the fix
was resolution, not scale.

**Why a detector instead of a vision-language model.** On a 28-symbol sheet,
this pipeline returned 28 detections, 26 correctly classed, in 0.5 s. A
general-purpose VLM shown the same image returned 28 by coincidence and
invented categories that are not in the symbol legend — *"Check valve 2"*,
*"Hnad-op"* — in 30.5 s. The published literature agrees: image-only prompting
scores 36.7–41.3% exact match; the same models constrained to query a
recovered graph score 74.3–76.0%
([arXiv 2609.05880](https://arxiv.org/abs/2609.05880)). The variable that
moves the number is whether a graph exists, not how large the model is — which
is the whole argument for training this detector rather than asking a bigger
VLM to read the drawing directly.

Full method, the OCR-recovers-0%-of-tags bug this caught, and every caveat:
[`docs/BENCHMARK-PID.md`](../docs/BENCHMARK-PID.md).
