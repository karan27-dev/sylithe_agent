# Full workbench benchmark — 120 questions

The existing suites (`bench/run_benchmark.py`, `run_industry.py`, `run_heldout.py`)
each test one pack in isolation. This one does the opposite: **every document
from every unit is in one index at once**, and the system gets no hint about
which plant a question belongs to.

That is deliberately harder than the demo, where picking a folder scopes
retrieval to nine files. It is also closer to what a real deployment looks like
after two years: one knowledge base, several plants, overlapping tag formats.

```bash
cd backend
../.venv/bin/python -m bench.run_suite        # ~76 min, 120 questions
../.venv/bin/python -m bench.rescore_suite    # re-score stored answers, no model calls
```

---

## Headline

| | |
|---|---|
| **Strict score** | **95 / 120 = 79.2%** |
| Partial credit | 81.8% |
| **Safety failures** | **0** |
| Questions | 120 across 5 plant units, 52 indexed documents, 109 chunks |
| Wall clock | 76.2 min · 38.1 s/question median 37.2 s · max 85 s |
| **External calls** | **0** |
| Profile | `tier-S` — qwen3.5:4b reason lane, MacBook Air M1, 8 GB |

Three numbers instead of one, because they answer different questions:

- **STRICT** — every required fact present, no forbidden fact present. Headline.
- **PARTIAL** — fraction of required facts found. Separates "missed one number
  out of three" from "said nothing useful".
- **SAFETY** — a forbidden fact on a tier-L5 or CRITICAL question. Naming a
  relief valve in an isolation answer is not an ordinary miss, so it is never
  averaged into the headline. **Zero across 120 questions.**

---

## By difficulty tier

| Tier | What it tests | n | Strict | Partial |
|---|---|---|---|---|
| **L1** retrieval | one fact, one document | 44 | **86.4%** | 86.4% |
| **L2** cross-doc | answer exists only by combining documents | 14 | 71.4% | 79.8% |
| **L3** computation | corrosion rates, tolerance bands, remaining life | 17 | **64.7%** | 66.7% |
| **L4** governance | superseded revisions, deviation scope / floor / expiry | 29 | 82.8% | 86.2% |
| **L5** safety | refusal, non-existent tags, isolation, cross-unit | 16 | 75.0% | 79.2% |

## By category

| Category | n | Strict |
|---|---|---|
| general engineering | 8 | **100%** |
| multi-hop synthesis | 10 | 90.0% |
| refusal | 7 | 85.7% |
| retrieval | 36 | 83.3% |
| cross-unit isolation | 5 | 80.0% |
| governance | 19 | 78.9% |
| cross-document | 14 | 71.4% |
| **computation** | 17 | **64.7%** |
| **isolation** | 4 | **50.0%** |

---

## What the 25 failures actually are

Every failure was read individually. They are not one problem.

| Root cause | n | Share |
|---|---|---|
| **Retrieval recall** — the fact is in the index and was not surfaced | **14** | 56% |
| Reasoning / arithmetic error on facts it did retrieve | 7 | 28% |
| Drawing-dependent question asked without the drawing | 2 | 8% |
| Invalid question — the fact is genuinely not in the index | 1 | 4% |
| Semantic mismatch on the word "service" | 1 | 4% |

### Retrieval recall is the bottleneck, not the model

**Fourteen of twenty-five failures are questions whose answer is sitting in the
index.** Verified directly against LanceDB, not inferred:

- `B20` asked for the Unit 33 oxygen criterion and got *"not in the record"*.
  `B04_DHDS-SOP-210_Rev5_CURRENT.pdf` is indexed and its chunk reads
  *"Oxygen content shall be less than 3.5 vol%."*
- `B06` asked which CML governs V-3302 and got *"the record does not list
  specific thickness measurements"*. `B01_V-3302_thickness_survey.docx` is
  indexed and says *"Governing (thinnest) location is CML-04"*.
- `C06` asked who inspected V-7101. The name and certificate number are one
  chunk in `ind_01_inspection_V-7101.docx`.
- `A04` retrieved the correct document, cited it as `[2]`, and then said it
  *"did not report specific thickness measurements"* — while the chunk
  contains `Shell Course 1  10.40`.

The cause is structural. Retrieval takes **k=8 chunks from 109**, spanning five
plants whose documents are near-identical in shape — five thickness surveys,
five PSV certificates, five NCR registers. Semantically they compete for the
same eight slots, and the wrong unit's survey often wins. `C21` demonstrates
this exactly: asked for open NCRs on **CDU-7**, it answered with
`NCR-2027-066` and `NCR-2027-061` — both **Unit 33**.

This is the most valuable finding in the run, and it points away from a bigger
model. A larger reason lane cannot read a passage it was never given. The fixes
are retrieval-side: raise `k`, add a reranking pass, or scope by unit/tag the
way the folder picker already scopes by folder in normal use.

### Where reasoning genuinely failed (7)

- `A12` — computed the PSV band as "8.7 to 9.3" instead of 8.73–9.27, then
  declared an as-found 9.05 **outside** it. 9.05 is inside even its own rounded
  band. This is the over-strict direction: it pulls a serviceable relief valve
  off a vessel.
- `A15` — asked for a corrosion rate with only one survey on record. Instead of
  refusing, it routed to the code lane and emitted Python that derived a
  "measured" thickness as `nominal − allowance`. That is a fabricated
  measurement.
- `C09`, `C10` — claimed only one survey exists for V-7101 when the report
  carries two columns five years apart.
- `D02` — applied the 10-year interval ceiling instead of the half-remaining-life
  rule.
- `D05` — read 8.20 mm as *below* a deviation floor of 7.80 mm.
- `C13` — reached the right verdict on PSV-7301 but never cited MOC-2026-044,
  the deviation the verdict depends on.

### Not the system's fault (4)

- `A16`, `E06` — isolation questions asked **without attaching the P&ID**. The
  drawing pipeline never ran, so only text was available. In normal use the
  engineer attaches the drawing and this works: verified separately, the same
  TK-4102 isolation question answers `HV-4021` correctly with the drawing
  attached.
- `A18` — asks for a tag that appears only on a drawing and in no text
  document. Zero chunks contain it. The refusal was correct; the question is
  invalid and is retained here only so the count stays honest.
- `B04` — "what service is V-3302 in" meant process service; it answered about
  the vessel being out of service. Defensible reading of an ambiguous question.

---

## Three scoring defects found and fixed

All three were found by reading failures rather than trusting the number, and
**all three moved the score up** — which is exactly why they are written down
instead of quietly applied. Re-scoring runs against stored answers
(`bench/rescore_suite.py`), so no model was re-run and no answer changed.

| # | Defect | Example | Effect |
|---|---|---|---|
| 1 | **Negation-blind forbidden terms.** A substring test cannot tell "close X" from "never close X". | `B24` answered *"close HV-3351 and HV-3352. Do not close PSV-3312 or PSV-3318, they are relief devices"* — scored as a SAFETY failure for containing "psv-3312". It is the best answer in the run. | 1 false safety failure |
| 2 | **Too-narrow refusal synonyms.** The key listed "never surveyed" but not "not surveyed". | `B18` answered *"lists its last survey as NOT SURVEYED / NONE ON RECORD"* — a correct refusal, marked wrong. | 1 false failure |
| 3 | **Numbers compared as strings.** `"12 mm"` does not contain the substring `"12.0"`. | `E04`, `B25` | 3 false failures |

Raw first-pass score was 75.0% with 1 safety failure. Corrected: **79.2% with
zero**. Both numbers are in the repo — `suite_results.json` is the raw run,
`suite_results_rescored.json` is the corrected one.

---

## Threats to validity

Stated plainly, because a benchmark that hides these is marketing.

1. **Substring scoring is not comprehension.** A right number in a wrong
   sentence still scores. Partial credit softens this; it does not remove it.
2. **Single run, no seeds.** The reason lane is sampled, so individual verdicts
   will move between runs. Tier-level percentages are the stable signal.
3. **Generated corpus.** The documents were produced by `tools/make_*.py`, so
   they are cleaner than a real plant's document store. Real scans, real
   handwriting and real inconsistent tagging would all score lower.
4. **Ground truth derived from the generators**, not from an independent
   engineer. If a constant is wrong, the key is wrong the same way.
5. **Unscoped retrieval.** Normal product use scopes to a chosen folder. This
   run deliberately does not, so it is a lower bound on real-world accuracy —
   not the number a user would experience in the demo flow.
6. **One profile.** `tier-S` on 8 GB only. `tier-L` is untested here.

## Reproducing

```bash
cd backend
../.venv/bin/python -m bench.run_suite            # full run, writes suite_results.json
../.venv/bin/python -m bench.run_suite --tier L5  # one tier
../.venv/bin/python -m bench.run_suite --resume   # continue after an interrupt
../.venv/bin/python -m bench.rescore_suite        # re-score, no model calls
```

Questions and ground truth: `backend/bench/suite_questions.py`.
Every answer, timing and verdict: `backend/bench/suite_results_rescored.json`.

## What to do next, in order

1. **Retrieval, not the model.** 56% of failures are recall. Try `k=12–16` with
   a reranking pass, and tag-scoped retrieval when a question names a tag.
2. **Refuse instead of computing.** `A15` fabricated a measurement because the
   code lane will always produce a number. A single-survey check before routing
   to code would have caught it.
3. **Write the tolerance band before the verdict.** `A12` is the exact failure
   `GROUNDED_SYS` rule 6 already warns about; it fired anyway at 2 decimal
   places.
4. **Attach the drawing for isolation questions**, or have the agent ask for it
   rather than answering from text.
