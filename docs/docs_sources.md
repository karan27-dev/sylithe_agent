# Where to get real documents to test with

Real plant documents are confidential, and most public "sample inspection
reports" sit behind Scribd-style paywalls. Three tiers of source, best first.

## 1. Generate a corpus (no download, covers every file type)

```
python -m tools.make_corpus --clean
python -m ingest.pipeline build --rebuild
```

Produces 13 files whose numbers agree with each other, so cross-document
questions work ("the report says 11.2 mm - what did the approval note say?"):

| File | Exercises |
|---|---|
| `inspection_report_TK4102.docx` | native text + tables |
| `approval_note.docx` | the engineering decision |
| `SOP-114_relief_devices.docx` | the spec being checked against |
| `ut_thickness_log.xlsx` | spreadsheet, 2 sheets |
| `ut_readings.csv` | csv |
| `preshutdown_review.pptx` | slides |
| `scanned_page.png` | scan -> OCR |
| `scanned_report.pdf` | image-only PDF -> OCR (no embedded text) |
| `nameplate_TK4102.jpg` | photo -> OCR / vision lane |
| `PID-CDU2-004.png` | P&ID schematic |
| `maintenance_bulletin.md` | markdown + table |
| `safety_circular.html` | html |
| `operator_shift_log.txt` | plain text |

## 2. Real industrial reports - free, public, directly downloadable

**US Chemical Safety Board** publishes complete refinery and plant
investigation reports as open PDFs. These are the closest public analogue to
what an employee actually uploads: equipment tables, process descriptions,
diagrams and photographs.

- Completed investigations index: https://www.csb.gov/investigations/completed-investigations/
- Example PDFs served directly:
  - https://www.csb.gov/assets/1/20/bp_transcript_1.pdf
  - https://csb.gov/assets/1/17/tesoro_draft_report_public_comments.pdf
  - https://www.csb.gov/assets/1/20/csb_responses_to_public_comments_on_tesoro_draft_report.pdf

**API 510** (pressure vessel inspection code) - the standard the reports are
written against, useful as an SOP document to check findings:
- Body of Knowledge (official, free): https://www.api.org/-/media/files/certification/icp/icp-certification-programs/510/2025/sept%202025%20510%20bok_final%201.pdf

## 3. Document-AI datasets - for stress-testing OCR and layout

These are real scanned documents, not synthetic, and are what docling itself
is benchmarked on.

| Dataset | Size | What it is | Link |
|---|---|---|---|
| **DocLayNet** | 80,863 pages | Human-annotated layout across financial reports, laws, manuals, patents, tenders, scientific papers. **CDLA-Permissive-1.0.** Built by the docling team. | https://huggingface.co/datasets/ds4sd/DocLayNet |
| **FUNSD** | 199 forms | Noisy scanned forms, fully annotated. The hardest OCR case - filled-in forms. | https://guillaumejaume.github.io/FUNSD/ |
| **RVL-CDIP** | 400,000 images | 16 classes of low-quality 100-dpi scans from the 1980s-90s. Good for proving OCR survives bad input. | https://huggingface.co/datasets/aharley/rvl_cdip |

> Note: downloading these needs network, so do it on the build machine
> **before** sealing. Once the files are on disk, ingest runs air-gapped.

## How an employee actually loads documents

1. Drag files onto the composer, or click **Attach**. They are copied into
   `data/corpus/`, converted, OCR'd if needed, embedded and indexed locally.
2. Or drop files straight into `data/corpus/` and restart - `start.sh`
   re-indexes only what changed.

Supported extensions: `.pdf .docx .pptx .xlsx .csv .md .html .htm .txt
.png .jpg .jpeg .tiff .bmp .webp`
