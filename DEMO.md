# Demo script

```bash
./start.sh          # → http://127.0.0.1:8000
```

Upload with **Attach**, or drag onto the message box. Watch the activity panel
above each answer - it shows which model was picked and why.

Files live in `test-documents/`. Regenerate any time with
`cd backend && ../.venv/bin/python -m tools.make_corpus`.

---

## 1. Grounded answers with citations  (2 min)

**Upload** everything in `test-documents/1-works/` (9 files).

| Ask | What to look for |
|---|---|
| `What deviation was found on TK-4102?` | 11.2 mm vs nominal 12.0, with `[1]` citations. Click a citation - the exact passage opens. |
| `Does the PSV-2041 set pressure meet SOP-114?` | 12.5 barg against a required 14.0. **This answer spans two documents** - the reading is in the inspection report, the requirement is in the SOP. |
| `What is the shell thickness of TK-9999?` | *"not in the record"*. There is no TK-9999. Inventing one here would be the worst failure in the system, so this is the most important question in the demo. |
| `What is today's dollar exchange rate?` | Declines. The machine is sealed; it has no live data and says so instead of guessing. |

---

## 2. Real deliverables, not chat replies  (3 min)

The PS asks for "real deliverables ... not just chat replies".

| Ask | Result |
|---|---|
| `Draft an approval note for TK-4102 as a Word file` | A **.docx** download card appears. Open it: findings with citations, a measurements table, a decision, a sign-off, and a **Sources page** mapping every `[n]` to a real file. |
| `Make an Excel sheet of the UT thickness readings` | A **.xlsx** with real formulas, not pasted values. |
| `Create a PowerPoint for the pre-shutdown review` | A **.pptx**, one slide per finding, plus a sources slide. |

This is the PS's flagship demo end to end, about 45-75 s.

---

## 3. Scans and handwriting  (2 min)

**Upload** `test-documents/2-scanned-ocr/` and
`backend/data/corpus/handwritten_shift_log.png`.

| Ask | What to look for |
|---|---|
| `Which equipment tags are in the scanned report?` | `scanned_report.pdf` has **no embedded text at all** - it was OCR'd locally. |
| `Read the handwritten shift log and give the NCR number exactly` | `NCR-2026-0088`. The activity panel says **"Reading the image"** - the model is looking at the page, not reading an OCR transcript. Plain OCR loses this line. |

---

## 4. Engineering drawings  (3 min)

**Upload** `test-documents/3-known-gaps/PID-CDU2-004.png`.

The chip will say **"no text found"**. That is correct and worth pointing out:
a drawing's content is geometry, not text, so the document pipeline finds
nothing. The system recognises it as a drawing and routes it elsewhere.

| Ask | What to look for |
|---|---|
| `What do I need to close to isolate TK-4102 on this P&ID?` | **HV-4021.** One path leaves the tank, so one valve isolates it. It does **not** list PSV-2041 - a relief valve is never closed to isolate equipment. |
| `What equipment is on this drawing, and what is PSV-2041 set at?` | Equipment from the drawing; **12.5 barg from the documents**. That pressure is written nowhere on the P&ID. The drawing supplies structure and a tag, the documents supply the value, and the tag joins them. |

Your own drawings in `~/Downloads/P-ID Symbols.v1i.coco/test/` work too - one of
them yields 22 ISA tags.

---

## 5. Code that actually runs  (2 min)

| Ask | What to look for |
|---|---|
| `Write and run a python script to calculate the corrosion rate for TK-4102` | The activity panel shows **"Running the code"** and the printed output appears: 0.80 mm loss, 0.40 mm/yr, 2.0 yr remaining. It was executed, not just written. |
| `Write a script that downloads the API 653 standard from the internet` | **`blocked=network`**. The sandbox denies it. Generated code cannot leave the machine even though it is a separate process. |

---

## 6. The sovereignty claim  (1 min)

Click **Air-gapped** in the sidebar at any point.

`EXTERNAL CALLS: 0` is not a badge. It reads from the object that does the
blocking - `core/airgap.py` intercepts every socket and DNS call before it
leaves. `backend/logs/network.jsonl` is the permanent record, and it already
contains three blocked attempts to reach `huggingface.co` from a library
checking for model updates during development. That is the feature working.

---

## Suggested 10-minute order

1. Upload `1-works/`, ask the deviation question, click a citation
2. Ask about TK-9999 - show the refusal
3. Ask for the approval note, open the .docx
4. Upload the P&ID, ask the isolation question
5. Ask what PSV-2041 is set at - point out the number is not on the drawing
6. Run the corrosion calculation, then the download attempt
7. Open the network monitor

## What to say if asked about weak spots

Say them before you are asked:

- Line tracing over-connects on symbol-legend sheets. Real process drawings are
  fine; a sheet of symbols in a grid is not.
- A 2B model mixes up rows when one retrieved chunk holds two pieces of
  equipment. `P-4110A` is the known case.
- Handwriting was tested with a handwriting font, not real cursive.
- Everything runs on an 8 GB laptop. `models.yaml` has a `tier-L` profile that
  points at a GPU server, and the answers get better without touching the code.
