# Sovereign Workbench

An on-premise, air-gapped AI workbench for confidential industrial work.
Reads plant documents, scans and drawings, answers with citations, and
produces real Word / Excel / PowerPoint deliverables — with **zero external
network calls**, enforced at the socket layer rather than promised in a slide.

Built for **SIH 2026 · Problem Statement 26117**, submitted by
**Mangalore Refinery and Petrochemicals Limited (MRPL)**, an ONGC subsidiary
under the Ministry of Petroleum & Natural Gas.

```bash
./start.sh          # → http://127.0.0.1:8000
```

---

## Table of contents

- [Why this is actually sovereign](#why-this-is-actually-sovereign)
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Install](#install)
- [Usage](#usage)
- [Adding a model — 6 lines, no code](#adding-a-model--6-lines-no-code)
- [Measured performance](#measured-performance)
- [Engineering decisions worth reading](#engineering-decisions-worth-reading)
- [PS 26117 compliance](#ps-26117-compliance)
- [Roadmap](#roadmap)

---

## Why this is actually sovereign

The claim is not "we don't call the cloud." The claim is **"we cannot"**, and
the proof is on screen while you use it.

`backend/core/airgap.py` monkey-patches `socket.connect`, `socket.connect_ex`
and `socket.getaddrinfo` before anything else imports. Every outbound attempt
is classified `local` / `lan` / `EXTERNAL`, appended to
`backend/logs/network.jsonl`, and under `seal()` any EXTERNAL attempt raises
`SovereigntyViolation`. DNS is blocked too — a lookup leaks the query even if
the connection never opens.

The sidebar counter polls that same in-process monitor. It is not decorative:
it is the object that does the blocking.

> During development this caught docling trying to reach `huggingface.co`
> three times to check for model updates. All three were blocked, and they are
> still in the log. That is the feature working.

### Stack choices made *because* of the air gap

| Rejected | Why |
|---|---|
| **Streamlit / Gradio** | Both send usage telemetry by default. On a sovereignty demo our own network log would catch the leak on stage. |
| **Next.js / React** | Needs Node + `npm install` + a build step on the sealed box, and the default template pulls Google Fonts from a CDN. |
| **Any CDN, web font, or `marked.js`** | Blocked when sealed. The markdown renderer is 83 lines of hand-written JS. |
| **`deepseek-ocr` / `glm-ocr` (6.7 GB)** | A dedicated OCR VLM would help on handwriting, but it cannot fit beside anything else on 8 GB. RapidOCR (61 MB ONNX) already handles our scans. |

Result: **no npm, no CDN, no build step.** FastAPI + vanilla HTML/CSS/JS over
Server-Sent Events.

---

## What it does

**Ask questions about your documents.** Retrieval runs over a local LanceDB
index; every claim carries a `[1]` citation you can click to see the exact
passage, file and page.

**Produce real files, not chat replies.** Ask for "an approval note as a Word
file" and you get a `.docx` on disk — findings with citations, a measurements
table, a decision, a sign-off block, and a Sources page mapping every
reference back to a source document.

**Show its work.** A live activity feed shows each phase as it happens: which
model was selected and *why*, how many passages cleared the relevance
threshold, how many tokens came back, and what file was written.

**Read almost anything.** `.pdf .docx .pptx .xlsx .csv .md .html .txt
.png .jpg .jpeg .tiff .bmp .webp` — including image-only PDFs, which are
OCR'd locally.

**Prove it stayed local.** The network monitor, always visible.

---

## Architecture

```
                 ┌─────────────────────────────────────────┐
   browser ──────┤ frontend/  (no build step, no CDN)       │
      ▲          │  index.html · app.css · main.js · md.js  │
      │ SSE      └─────────────────────────────────────────┘
      │
┌─────┴──────────────────────────────────────────────────────┐
│ backend/api/app.py        FastAPI · SSE bridge · uploads    │
│ backend/api/chats.py      multi-turn history (JSON on disk) │
├────────────────────────────────────────────────────────────┤
│ backend/agents/workbench.py                                 │
│   plan → select model → retrieve → answer → build spec →    │
│   write file.  Yields typed events; the UI renders them.    │
├──────────────────────┬─────────────────────────────────────┤
│ backend/core/llm.py  │ backend/ingest/pipeline.py           │
│  lane router         │  docling → chunks → embeddings →     │
│  ollama + OpenAI     │  LanceDB, incremental by fingerprint │
│  hot-swap, fallback  │                                      │
├──────────────────────┴─────────────────────────────────────┤
│ backend/tools/deliverables.py   docx · xlsx · pptx writers  │
│ backend/core/airgap.py          socket + DNS interceptor    │
└────────────────────────────────────────────────────────────┘
            ↕ localhost only
      Ollama (qwen3.5 · qwen2.5-coder · nomic-embed-text)
```

### The lane abstraction

Callers never name a model. They ask for a **lane** — `router`, `reason`,
`code`, `vision`, `embed` — and `models.yaml` decides which model serves it.
That is what makes "new open weight models should be addable later without
redesigning the system" true rather than aspirational.

### Why the model doesn't write the file

The model returns **structured JSON**; Python renders the OOXML. A 2 B model
cannot be trusted to emit valid `.docx`, but it can fill a schema. The split
also means a malformed response degrades into a partly-empty document instead
of a crash.

---

## Repository layout

```
backend/
├── core/
│   ├── airgap.py        socket + DNS interceptor; audit() / seal()
│   └── llm.py           lane router; never names a model itself
├── ingest/
│   └── pipeline.py      docling → chunks → embeddings → LanceDB
├── agents/
│   └── workbench.py     the plan/act loop; yields UI events
├── tools/
│   ├── deliverables.py  .docx / .xlsx / .pptx writers
│   └── make_corpus.py   generates a 13-file demo corpus
├── api/
│   ├── app.py           FastAPI, SSE, uploads, downloads
│   └── chats.py         conversation store
├── models/models.yaml   ← the only file that names a model
├── data/                corpus, index, deliverables (gitignored)
└── requirements.txt

frontend/
├── index.html           markup
├── app.css              light + dark themes
├── main.js              activity feed, streaming, chats
└── md.js                83-line markdown renderer

docs/
├── PS26117.md           requirement-by-requirement compliance
└── docs_sources.md      where to get real test documents
```

---

## Install

Needs network **once**, on the build machine. After this, unplug.

```bash
# 1. Ollama + models  (~5 GB)
brew install ollama && ollama serve &
ollama pull qwen3.5:0.8b      # router
ollama pull qwen3.5:2b        # reason + vision (natively multimodal)
ollama pull qwen2.5-coder:1.5b
ollama pull nomic-embed-text

# 2. Python deps  (~2 GB, includes torch + docling)
python3.11 -m venv .venv
./.venv/bin/pip install -r backend/requirements.txt

# 3. Cache docling's layout + OCR weights (~1.3 GB) while still online
cd backend && ../.venv/bin/python -c "
from docling.utils.model_downloader import download_models; download_models()"

# 4. Build a demo corpus and index it
../.venv/bin/python -m tools.make_corpus --clean
../.venv/bin/python -m ingest.pipeline build --rebuild
```

Now pull the cable. Nothing above is needed again.

---

## Usage

```bash
./start.sh                                   # checks Ollama, reindexes, serves
```

```bash
cd backend
../.venv/bin/python -m core.llm              # lane self-test with timings
../.venv/bin/python -m ingest.pipeline build # index new/changed files only
../.venv/bin/python -m ingest.pipeline search "shell thickness"
../.venv/bin/python -m ingest.pipeline status
../.venv/bin/python -m tools.make_corpus     # regenerate the demo corpus
```

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/boot` | engine health, lanes, index size, seal state |
| `GET /api/ask?q=&chat_id=` | **SSE** stream of agent events |
| `GET /api/sovereignty` | live monitor + recent socket attempts |
| `GET/POST/DELETE /api/chats` | conversation history |
| `POST /api/upload` | ingest a file immediately |
| `GET /api/deliverables/{name}` | download a produced file |

### Agent event types

`step` · `plan` · `route` · `sources` · `token` · `thinking` · `file` ·
`done` · `error`. The agent is the single source of truth about what
happened; the UI only renders what it emits.

---

## Adding a model — 6 lines, no code

`models.yaml` is the only file that names a model.

```yaml
  tier-M:
    endpoint: http://localhost:11434
    fallback_profile: tier-S
    lanes:
      reason: { model: qwen3.5:4b, think: false, max_tokens: 1024 }
```

Switch with `active_profile: tier-M`. Three profiles ship:

| Profile | Reason lane | For |
|---|---|---|
| `tier-S` | `qwen3.5:2b` | 8 GB laptop, demo speed |
| `tier-M` | `qwen3.5:4b` | same laptop, better grounding |
| `tier-L` | `qwen3.5:27b` | LAN GPU box via vLLM (OpenAI-compatible) |

Two backends are supported and inferred from the endpoint. If a `tier-L` GPU
node doesn't answer in 2 s, the call silently falls back to `tier-S` — and the
UI labels that answer `fell back → tier-S`, so nobody is misled about what
produced it.

Adding a routing class works the same way: add `examples:` beside it and the
few-shot prompt updates itself.

---

## Measured performance

MacBook Air M1, 8 GB. Every number here was measured, not estimated.

| Operation | Result |
|---|---|
| Router classification | 0.9 s · **93% lane accuracy** (15 held-out queries) |
| Retrieval (112 chunks) | 2.4–2.8 s |
| Answer generation | 8–18 s · 4–6 tok/s · `qwen3.5:2b` |
| **Full Q&A** | **~22 s** |
| **Question → Word file** | **~74 s** |
| Ingest, 13 files incl. 2 scans | 30 s (OCR included) |
| Ingest, real 381-page PDF | 3.3 s/page → ~21 min one-time |
| Re-run, nothing changed | 0.0 s (fingerprint skip) |
| **External calls** | **0** |

### Benchmarked on MRPL's own public filing

MRPL Annual Report 2024-25 (381 pages, 13 MB), downloaded from the company's
own site. 380/381 pages carry embedded text; only the cover needs OCR.

| Question | Result |
|---|---|
| Crude oil prices in FY 2024-25? | "declined from about USD 90 to around USD 70 per barrel" — **verified verbatim** |
| Refinery throughput? | "18.04 MMT, 120% capacity utilisation, earlier best 114%, 15.5% ethanol blending" — **all four figures verified** |
| MRPL's Net Zero target year? | "not in the passages" — **correct refusal.** The target lies outside the ingested slice; the slice mentions only *India's* 2070 target, and the model did not conflate them. |

---

## Engineering decisions worth reading

Each of these was a bug found by measuring rather than assuming. All are
recorded in `models.yaml` next to the setting they justify.

**1. `think: true` on a 2 B model returns an empty answer, not an error.**
`qwen3.5:2b` needs ~2300 tokens of reasoning before it emits any content. At
`max_tokens: 2048` the budget is spent thinking and `content` comes back as
`""` — silently. Measured: 31.4 s / full answer with thinking off, vs 96.2 s /
**zero characters** with it on. The reason lane now runs `think: false`, and
`llm.py` detects the truncation and escalates (4096 tokens → then
`think=false`) so a blank answer is impossible.

**2. The 0.8 B router collapsed onto the first-listed class.** It answered
`code` for everything, scoring 83%. Few-shot examples — in YAML, not code —
took it to 100% on a 12-query held-out set. Note that **lane** accuracy
matters more than **class** accuracy: `document` and `reason` both map to the
reason lane and both retrieve, so confusion between them changes nothing
observable.

**3. Unconditional retrieval made "hi" summarise an inspection report.**
Vector search always returns *k* results however poor the match, and the
grounded prompt then ordered the model to cite them. Measured separation is
clean — chitchat scores 0.427–0.477, real questions 0.546–0.586 — so there is
now a 0.50 floor plus a `chitchat` class that skips retrieval entirely.

**4. A JSON parser that "succeeded" on the wrong object.** The model emitted a
document spec *without* the opening brace, so brace-matching locked onto the
nested `"meta": {...}` block, parsed it fine, and would have written an empty
document while reporting success. The parser now validates that the result
carries the payload key for its document kind.

**5. `lancedb.list_tables()` returns a response object, not a list.** So
`TABLE in db.list_tables()` was silently always `False` and `create_table`
then failed with "already exists". Normalised in one helper.

---

## PS 26117 compliance

Full requirement-by-requirement table in [`docs/PS26117.md`](docs/PS26117.md).

**Done** — air-gap proof · multi-model support · automatic model selection ·
new models addable via YAML · local knowledge base with citations · OCR and
vision · **real Word/Excel/PowerPoint deliverables** · the flagship demo
(scanned report → findings → approval note as a Word file) · visible network
monitor.

**Not yet** — sandboxed code execution · P&ID graph extraction · handwriting
evaluation.

---

## Roadmap

1. **Sandboxed code execution** — subprocess with no network (the air-gap
   patch already denies it), temp cwd, wall-clock timeout.
2. **`analyze_pid`** — OpenCV + networkx graph over the drawing. Already
   declared as `pre_tool` in `models.yaml`; a P&ID currently yields 0 chunks
   because layout analysis finds no text in a line drawing.
3. **Handwriting** — evaluate GOT-OCR2 (580 M) against RapidOCR on field notes.
4. **Multi-step iteration** — the agent currently runs one plan; let it revise
   after seeing tool output.

---

## License

Specify before publishing. The models carry their own licenses
(Qwen: Apache-2.0; nomic-embed-text: Apache-2.0).
