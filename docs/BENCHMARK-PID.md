# P&ID benchmark

Twenty drawings, four difficulty tiers, exact ground truth, and the documents
that carry the numbers. Built because the project's own test drawings were all
small, and a small drawing hides the failure that matters: a real plant P&ID is
around 7168 x 4562, and the pipeline behaved completely differently on one.

Everything here is reproducible offline:

```bash
python -m tools.make_pid_bench_real      # detection set  -> data/bench_pid_real
python -m tools.make_pid_bench           # structure set  -> data/bench_pid
python -m bench.run_pid_bench            # score detection (as shipped)
python -m bench.run_pid_bench --no-tile  # control: single pass
python -m bench.run_pid_bench --synth    # score tags and connectivity
```

## Why there are two sets

The obvious way to build this is to crop symbols and paste them onto a blank
sheet. Measured, that does not work - and the measurement is worth recording,
because the number it produces looks like a model result and is not:

| sheet | symbols located |
|---|---|
| original held-out test sheets | **92.3%** |
| same symbols pasted onto a blank sheet | 58% |

No paste mode closes the gap (opaque 7/12, native 4/12, resized 4/12,
thresholded 2/12). A lone glyph on an empty page is simply not what the
detector was trained on. A composited sheet therefore scores the composition,
not the detector.

So detection is scored only on real pixels:

- **`bench_pid_real/` - detection.** Whole held-out test sheets are laid out as
  panels on one large canvas. Every pixel and all local context is real
  drawing; ground truth is the dataset's own annotations shifted by the panel
  offset. The only synthetic thing is the arrangement, which is the variable
  under test: does this still work when the sheet is big?
- **`bench_pid/` - structure.** Generated sheets with known ISA tags and known
  connections. Its detection column is not trustworthy for the reason above,
  but tags are really rendered text and pipes are really drawn lines, so tag
  reading and connectivity are measured here.

Symbols in both sets come from the **held-out test split** - 97 sheets the
detector never trained on.

## Tiers

| tier | sheets | symbols/sheet | canvas |
|---|---|---|---|
| easy | 5 | 6-9 | 820x880 - 1500x2240 |
| hard | 5 | 12-20 | 2180x1560 - 2180x2240 |
| very_hard | 5 | 22-30 | 1500x880 - 2860x1560 |
| complex | 5 | 34-48 | 3540x880 - 4220x1560 |

20 sheets, 496 annotated symbols (detection set); 433 symbols and 413
connections (structure set).

## Results

### Detection - real pixels, IoU 0.5, class-agnostic

| tier | symbols | tiled (shipped) | single pass (control) |
|---|---|---|---|
| easy | 49 | **91.8%** | 79.6% |
| hard | 86 | **77.9%** | 39.5% |
| very_hard | 141 | **83.0%** | 51.8% |
| complex | 220 | **80.0%** | 17.7% |
| **all** | **496** | **81.7%** | **37.3%** |

Class-correct 76.4% vs 32.5%. F1 **0.770** vs 0.503. Precision 72.8% vs 77.4% -
tiling trades a little precision for more than double the recall.

Read the control column downwards. Without tiling the pipeline degrades as the
sheet grows, from 79.6% to **17.7%**; with tiling that slope nearly disappears
(91.8% to 80.0%). On the hardest tier tiling is worth **4.5x**.

This is the whole "22 of 24" story. The old test drawing was easy-tier, so it
scored well and looked fine. A full-size plant sheet is complex-tier, where the
shipped pipeline was finding under a fifth of the symbols.

### Tags - structure set

| tier | before | after |
|---|---|---|
| easy | 90.6% | 90.6% |
| hard | 90.2% | 87.8% |
| very_hard | 67.5% | 88.6% |
| complex | **0.0%** | **84.9%** |
| **all** | **40.9%** | **86.8%** |

The benchmark found a second copy of the first bug. The detector had been
tiled; the OCR pass had not, and RapidOCR resizes internally to a few hundred
pixels on the long side. On a 6400 px sheet the same reader returned `ere`,
`3 102`, `ze` where a 1600 px sheet gave `TB-101`, `GV-102`. Symbols were being
found and then had no name - and without a tag there is no isolation answer,
however good detection is. Tiling the OCR pass fixed it.

### Connectivity - structure set

| tier | edges F1 |
|---|---|
| easy | 0.423 |
| hard | 0.172 |
| very_hard | 0.185 |
| complex | 0.136 |
| **all** | **0.179** |

This is the weakest stage and is reported as such. For reference, the published
state of the art on Digitize-PID reports F1 0.742 on exact process connections
(arXiv 2609.05880), so there is a long way to go. Line tracing is the next
thing to work on, not detection.

## Documents

`bench_pid/records.md` and `records.csv` carry a design pressure, set pressure,
measured thickness and inspection date for every tag. A P&ID never states a set
pressure - it states a tag - so the tag-to-value join is testable end to end:
the drawing supplies structure, the documents supply numbers.

## Caveats

- Detection numbers come from real pixels in a synthetic arrangement. Panel
  seams are a layout artefact a real sheet would not have.
- The structure set's detection column is not a valid model score. It is left
  in the output only so the two sets can be compared directly.
- Connectivity ground truth exists only in the structure set, because the
  source dataset annotates symbols and not connections.
- Ground truth is the source dataset's annotations. Any error in those is
  inherited here.
