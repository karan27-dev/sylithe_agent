# Benchmark

Two suites. Both score against ground truth taken from the source documents,
not against how an answer reads.

```bash
cd backend
../.venv/bin/python -m bench.run_benchmark    # 9 PS-requirement tasks
../.venv/bin/python -m bench.run_datasets     # 5 graded data sets, as a %
```

## Five graded data sets — **87.0%** (20/23)

| Set | Level | Score | Shape |
|---|---|---|---|
| set-1 | easy | **100%** | one drum, one valve, one relief device |
| set-2 | easy | **100%** | pump and filter in series |
| set-3 | medium | 80% | two parallel pumps, shared header |
| set-4 | medium | 50% | exchanger train, cross-document spec |
| set-5 | complex | **100%** | three-branch manifold, two relief devices, conflicting specs |

| | |
|---|---|
| easy | **100%** (8/8) |
| medium | 67% (6/9) |
| complex | **100%** (6/6) |
| **overall** | **87.0%** (20/23) |
| external calls | **0** |
| elapsed | 782 s |

Each set is indexed **alone**. Scoring against a combined index would let an
answer about T-301 be satisfied by a passage from set 5, which measures nothing
except how similar the numbers are.

The sets are generated, so ground truth is arithmetic done *before* the
documents were written. "0.40 mm/yr" is what the numbers give, not what the
system said last week. That distinction has bitten this project: a P&ID
expectation once encoded a spurious graph edge as correct, and fixing the graph
made the benchmark call the right answer wrong.

## The three remaining failures

Listed because they are the useful part.

**set-4 · "Does PSV-401 meet SOP-401?"** — the model said *"SOP-401 requires a
minimum of 12.5 barg"*. The SOP says **8.0 barg**. The conclusion (below the
requirement) is right, the number is invented. A 2B model hallucinating a
number it could have copied is the sharpest limitation in the system.

**set-4 · "What must be closed to isolate E-401?"** — answered *"the drawing
does not show a path out of E-401"*. The graph genuinely has no edges for the
exchanger: it is drawn as a rectangle, the detector does not classify it, and
the tag-anchored node does not reach the pipe. The model's behaviour is
correct — it reported an empty graph rather than inventing valves — but the
graph is wrong.

**set-3 · "Can P-301B be isolated while P-301A is running?"** — the answer
"No" is right, the reasoning is not: it argued from the drawing and called two
parallel pumps "in series", when the actual rule is SOP-301 clause 7, which
forbids isolating the spare. Fixing the earlier problem (the drawing being
ignored) pushed this one the other way.

## Nine PS-requirement tasks — 9/9

Retrieval, cross-document reasoning, refusing an unknown tag, refusing live
data, model auto-selection, Word and Excel deliverables, handwriting, and P&ID
isolation. Zero external calls throughout.

## What moved the number

| Change | Overall |
|---|---|
| first run | 73.9% |
| use the drawing whenever the question is about geometry | 82.6% |
| put the drawing *last* in the prompt, cap document lookups | **87.0%** |

The largest single gain came from ordering, not from a better model. With ten
tags looked up, the document block ran to thousands of characters and buried
the drawing at the top of the prompt — the model read the reports and never
reached the structure. Small models weight the end of a prompt.
