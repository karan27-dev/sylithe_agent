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

---

## MRPL provides NO dataset

The PS "Dataset Link" field contains **no link**. It reads, verbatim:

> "Open-source models and publicly available document samples (sample scanned
> PDFs, sample P&IDs from open datasets) to be used for demonstration; **no
> proprietary data required**."

So there is nothing to request from MRPL. Everything must be sourced from open
datasets or generated. That is what `tools/make_corpus.py` is for.

## Open-weight models — where they come from

| Model | Role | Source | Size |
|---|---|---|---|
| `qwen3.5:0.8b` | router | `ollama pull qwen3.5:0.8b` | 1.0 GB |
| `qwen3.5:2b` | reason + vision (natively multimodal) | `ollama pull qwen3.5:2b` | 2.7 GB |
| `qwen2.5-coder:1.5b` | code | `ollama pull qwen2.5-coder:1.5b` | 986 MB |
| `nomic-embed-text` | embeddings | `ollama pull nomic-embed-text` | 274 MB |
| `qwen3.5:4b` | tier-S reason | `ollama pull qwen3.5:4b` | 3.4 GB |
| docling layout + RapidOCR | OCR / layout | auto-cached to `~/.cache/docling/models` | 1.3 GB |

All Apache-2.0 or similarly permissive. Browse more at https://ollama.com/search
and https://huggingface.co/models.

## P&ID datasets — the one the PS names explicitly

| Dataset | Contents | Link |
|---|---|---|
| **Digitize-PID (Dataset-P&ID)** | **500 synthetic P&IDs**, 32 symbol classes, YOLO bounding boxes, train/val 4:1. The reference dataset in this field. | https://huggingface.co/datasets/hamzas/digitize-pid-yolo |
| Original release | Same data, Google Drive | https://drive.google.com/drive/u/1/folders/1gMm_YKBZtXB3qUKUpI-LF1HE_MgzwfeR |
| Paper | Digitize-PID, Paliwal et al. 2021 (TCS Research) | https://arxiv.org/abs/2109.03794 |
| Roboflow Universe | ~1,065 annotated P&ID images, 11 classes | https://universe.roboflow.com/pid-connect/p-id-symbols |

Fetch three samples:

```bash
for n in 0 1 10; do
  curl -L -o backend/data/pid_samples/pid_$n.jpg \
   "https://huggingface.co/datasets/hamzas/digitize-pid-yolo/resolve/main/DigitizePID_Dataset/images/train/$n.jpg"
done
```

### Measured: the document pipeline cannot read a P&ID at all

Tested on `pid_0.jpg` (7168x4561, 2.6 MB) from the dataset above:

```
docling convert -> 16.4 s
chars extracted  -> 14
content          -> '<!-- image -->'
```

Its YOLO label file lists **89 annotated symbols** on that page, so the content
is certainly there. Docling's layout model classifies the whole drawing as a
single picture and never passes the tag text to OCR — **OCR is not failing, it
is never invoked**.

This is why `analyze_pid` has to be a separate computer-vision tool (OpenCV for
symbols and lines, networkx for connectivity) rather than something the
document pipeline can be tuned into. It is already declared as `pre_tool` on
the `pid` routing class in `models.yaml`.
