# Test documents

Drag any of these onto the composer in the UI (or click Attach) to test ingest.
Every result below is measured, not predicted.

Regenerate the synthetic ones any time:
```bash
cd backend && ../.venv/bin/python -m tools.make_corpus
```

## 1-works/ — native text, indexes instantly

| File | Type | Chunks | Time | What it tests |
|---|---|---|---|---|
| `inspection_report_TK4102.docx` | Word | 3 | 0.1s | headings + tables |
| `approval_note.docx` | Word | 2 | 0.0s | the engineering decision |
| `SOP-114_relief_devices.docx` | Word | 2 | 1.0s | the spec findings are checked against |
| `ut_thickness_log.xlsx` | Excel | 2 | 0.0s | 2 sheets, numeric tables |
| `ut_readings.csv` | CSV | 1 | 0.0s | plain tabular |
| `preshutdown_review.pptx` | PowerPoint | 2 | 0.0s | slides |
| `maintenance_bulletin.md` | Markdown | 4 | 0.0s | markdown + table |
| `safety_circular.html` | HTML | 2 | 0.0s | html |
| `operator_shift_log.txt` | Text | 1 | 0.0s | plain text |

Good questions to ask after uploading these:

- "What deviation was found on TK-4102?"
- "Does the PSV-2041 set pressure match SOP-114?"
- "Draft an approval note for TK-4102 as a Word file"   ← produces a .docx
- "Make an Excel sheet of the thickness readings"        ← produces a .xlsx
- "Create a PowerPoint for the pre-shutdown review"      ← produces a .pptx

The numbers agree across files on purpose, so cross-document questions work:
the report says 11.2 mm, the approval note judges it against 10.4 mm minimum,
SOP-114 supplies the 14.0 barg requirement the PSV misses.

## 2-scanned-ocr/ — no embedded text, OCR runs locally

| File | Type | Chunks | Time | What it tests |
|---|---|---|---|---|
| `scanned_page.png` | scan | 3 | 5.1s | RapidOCR on a tilted, grainy form |
| `scanned_report.pdf` | image-only PDF | 3 | 3.8s | PDF with zero embedded text |
| `nameplate_TK4102.jpg` | photo | 1 | 4.5s | dense small text on a metal plate |

Ask: "Which equipment tags appear in the scanned report?"

## 3-known-gaps/ — these produce 0 chunks

| File | Type | Chunks | Time |
|---|---|---|---|
| `PID-CDU2-004.png` | synthetic P&ID | **0** | 1.4s |
| `real_pid_from_dataset.jpg` | real P&ID (Digitize-PID, 7168x4561) | **0** | 7.6s |
| `valve_actuator_symbols.png` | symbol legend | **0** | 1.5s |

All three return exactly `<!-- image -->`. Docling's layout model classifies a
line drawing as a single picture and never hands the tag text to OCR, so
**OCR is not failing - it is never invoked**. The real P&ID's annotation file
lists 89 symbols on the page, so the content is certainly there.

Two fixes, tracked in the roadmap:

1. Pass the uploaded image to the **vision lane**. `qwen3.5:2b` already reads
   the symbol legend well when called directly - it just never receives the
   image in the app flow.
2. **`analyze_pid`** - YOLO for symbols + OpenCV for pipe runs + networkx for
   connectivity, to answer structural questions ("trace TK-4102 to the PSV")
   that a description cannot.

## Not covered

- **Handwritten notes** - named in the PS, no sample yet, RapidOCR is built
  for printed text.
- **Old binary formats** `.doc .xls .ppt`, email `.msg .eml`, CAD `.dwg .dxf`,
  audio/video. Not supported and not planned.
