"""
Ingest - from paper to a searchable index, with no internet.

Requirement:
  documents, scans and drawings all processed on-premise, with the source
  shown alongside each answer (which file, which page).

Chain:
    file -> docling (layout + table + OCR, local weights)
         -> HierarchicalChunker (headings ke saath)
         -> merge/split char budget pe
         -> core.llm embed lane (nomic-embed-text, 768d)
         -> LanceDB table "corpus"

All weights are already in ~/.cache/docling/models. We set HF_HUB_OFFLINE=1
so docling does not even check for updates - airgap.seal() would block it
anyway, but not attempting is better than a blocked call.

Use:
    python -m ingest.pipeline build              # data/corpus -> index
    python -m ingest.pipeline search "TK-4102 thickness"
    python -m ingest.pipeline status
"""

from __future__ import annotations

import os

# BEFORE importing docling, otherwise it picks the network code path
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

import hashlib
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, Sequence

_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = _ROOT / "data" / "corpus"
INDEX_DIR = _ROOT / "data" / "index"
MANIFEST = INDEX_DIR / "manifest.json"
TABLE = "corpus"

DOCLING_ARTIFACTS = Path.home() / ".cache" / "docling" / "models"
RAPIDOCR_DIR = DOCLING_ARTIFACTS / "RapidOcr"

# Chunk budget in characters. HybridChunker wants an HF tokenizer (= network),
# so we merge/split on a character budget ourselves. nomic-embed-text has an
# 8192-token context; 1600 chars (~400 tokens) is safe and granular enough for
# retrieval.
CHUNK_CHARS = 1600
CHUNK_OVERLAP = 200
MIN_CHUNK_CHARS = 80

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".md", ".html",
             ".htm", ".txt", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}


def _quiet() -> None:
    """docling/RapidOCR log every model path at INFO - too noisy for a demo."""
    import logging
    for name in ("docling", "docling_core", "docling_ibm_models", "RapidOCR",
                 "rapidocr_onnxruntime", "transformers", "urllib3", "filelock"):
        logging.getLogger(name).setLevel(logging.ERROR)
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """One retrievable fragment, with a citation - otherwise answers cannot be trusted."""

    chunk_id: str
    source: str            # file name; this is what the UI shows
    path: str
    page: int              # 0 = unknown (docx/txt)
    heading: str           # breadcrumb: "Section > Subsection"
    kind: str              # text | table | ocr
    text: str

    def cite(self) -> str:
        loc = f"p.{self.page}" if self.page else "—"
        head = f" · {self.heading}" if self.heading else ""
        return f"{self.source} ({loc}){head}"


@dataclass
class Hit:
    chunk: Chunk
    score: float           # 0..1, higher is better

    def __str__(self) -> str:
        return f"[{self.score:.3f}] {self.chunk.cite()}\n{self.chunk.text[:200]}"


# ---------------------------------------------------------------------------
# docling converter
# ---------------------------------------------------------------------------


def _converter():
    """
    Offline converter. Without artifacts_path, docling reaches for HF.
    The RapidOCR ONNX paths are explicit too - the cache layout changes
    between versions and should not be guessed.
    """
    from docling.document_converter import (
        DocumentConverter, PdfFormatOption, ImageFormatOption,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions, RapidOcrOptions,
    )

    ocr = RapidOcrOptions(
        backend="onnxruntime",
        det_model_path=str(RAPIDOCR_DIR / "PP-OCRv6_det_small.onnx"),
        cls_model_path=str(RAPIDOCR_DIR / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
        rec_model_path=str(RAPIDOCR_DIR / "PP-OCRv6_rec_small.onnx"),
        rec_keys_path=str(RAPIDOCR_DIR / "ppocrv6_dict.txt"),
    )
    opts = PdfPipelineOptions(
        artifacts_path=str(DOCLING_ARTIFACTS),
        enable_remote_services=False,      # <- sovereignty, explicitly off
        do_ocr=True,
        do_table_structure=True,
        ocr_options=ocr,
    )
    opts.table_structure_options.do_cell_matching = True

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        }
    )


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------


def _page_of(item) -> int:
    """Page number from docling provenance, or 0 if unavailable."""
    try:
        prov = getattr(item, "prov", None) or []
        if prov:
            return int(prov[0].page_no)
    except Exception:
        pass
    return 0


def _split(text: str, budget: int = CHUNK_CHARS,
           overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split a large blob on the budget, at a sentence boundary where possible."""
    text = text.strip()
    if len(text) <= budget:
        return [text] if text else []

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + budget, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind(". "), window.rfind("\n"), window.rfind("। "))
            if cut > budget * 0.5:            # do not cut before the halfway point
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def _chunk_document(doc, source: Path) -> Iterator[Chunk]:
    """
    HierarchicalChunker gives a heading breadcrumb and needs no HF tokenizer,
    which is what the air gap requires.
    """
    from docling.chunking import HierarchicalChunker

    chunker = HierarchicalChunker()
    buf: list[str] = []
    buf_meta: tuple[int, str, str] | None = None

    def flush() -> Iterator[Chunk]:
        nonlocal buf, buf_meta
        if not buf or buf_meta is None:
            buf, buf_meta = [], None
            return
        page, heading, kind = buf_meta
        joined = "\n".join(buf).strip()
        for piece in _split(joined):
            if len(piece) >= MIN_CHUNK_CHARS or kind == "table":
                yield _mk(piece, source, page, heading, kind)
        buf, buf_meta = [], None

    for ck in chunker.chunk(doc):
        text = (ck.text or "").strip()
        if not text:
            continue
        meta = getattr(ck, "meta", None)
        headings = list(getattr(meta, "headings", None) or [])
        heading = " > ".join(h for h in headings if h)[:200]
        items = list(getattr(meta, "doc_items", None) or [])
        page = _page_of(items[0]) if items else 0
        label = str(getattr(items[0], "label", "")).lower() if items else ""
        kind = "table" if "table" in label else "text"

        key = (page, heading, kind)
        if buf_meta is not None and key != buf_meta:
            yield from flush()
        buf_meta = key
        buf.append(text)

        if sum(len(b) for b in buf) >= CHUNK_CHARS:
            yield from flush()

    yield from flush()


def _mk(text: str, source: Path, page: int, heading: str, kind: str) -> Chunk:
    cid = hashlib.sha1(
        f"{source.name}|{page}|{heading}|{text[:120]}".encode()
    ).hexdigest()[:16]
    return Chunk(
        chunk_id=cid,
        source=source.name,
        path=str(source),
        page=page,
        heading=heading,
        kind=kind,
        text=text,
    )


# Every deliverable this system writes carries this sentence on its Sources
# page. Finding it in an INPUT means we are about to index our own output.
SELF_MARKER = "Generated by Sovereign Workbench"

# Equipment tags: TK-4102, PSV-2041, P-4110A, NCR-2026-0088, SOP-114.
# Dense embeddings are poor at exact identifiers - measured here, searching for
# "P-4110A" returned TK-4102 chunks, because to the embedder the two tags look
# almost identical. Plant documents are made of tags, so that failure mode
# matters more here than in most corpora. Hence a lexical pass alongside the
# vector one.
QUERY_TAG = re.compile(r"\b([A-Z]{1,4})[-\s]?(\d{2,6})([A-Z])?\b")
TAG_BOOST = 0.25


def tags_in(text: str) -> list[str]:
    out = []
    for m in QUERY_TAG.finditer((text or "").upper()):
        t = f"{m.group(1)}-{m.group(2)}{m.group(3) or ''}"
        if t not in out:
            out.append(t)
    return out


def _mentions(chunk_text: str, tag: str) -> bool:
    """Match the tag however it was typed: TK-4102, TK 4102, TK4102."""
    a, b = tag.split("-", 1)
    pat = re.compile(rf"\b{re.escape(a)}[-\s]?{re.escape(b)}\b", re.I)
    return bool(pat.search(chunk_text))


class SelfGenerated(ValueError):
    """The file being ingested was produced by this system."""


def convert(path: Path, converter=None) -> list[Chunk]:
    """One file -> chunks. OCR kicks in automatically for scans."""
    _quiet()
    converter = converter or _converter()
    res = converter.convert(str(path))
    chunks = list(_chunk_document(res.document, path))

    # Refuse to index our own output. A generated approval note is a CONCLUSION
    # drawn from the corpus, not evidence in it. Indexed anyway, it competes
    # for retrieval slots against the reports it was derived from and the model
    # ends up citing itself - which launders a generated claim into a
    # "sourced" fact. Found in the wild: a generated note held 6 chunks, more
    # than any real document, and was winning retrieval.
    if any(SELF_MARKER in c.text for c in chunks):
        raise SelfGenerated(
            f"{path.name} was generated by this system - a deliverable is a "
            "conclusion, not a source. Not indexed.")

    if not chunks:
        # a scan where layout analysis yields nothing: fall back to markdown export
        md = (res.document.export_to_markdown() or "").strip()
        chunks = [
            _mk(p, path, 0, "", "ocr") for p in _split(md)
            if len(p) >= MIN_CHUNK_CHARS
        ]
    return chunks


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def _fingerprint(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def _load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save_manifest(m: dict) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2))


def _db():
    import lancedb
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(INDEX_DIR))


def _tables(db) -> list[str]:
    """
    In lancedb 0.38 list_tables() returns a ListTablesResponse, not a plain
    list, so `TABLE in db.list_tables()` is silently always False and
    create_table then fails with "already exists". table_names() returns the
    right thing but is deprecated. Normalise both in one place.
    """
    lt = getattr(db, "list_tables", None)
    if lt is not None:
        res = lt()
        return list(getattr(res, "tables", res) or [])
    return list(db.table_names())


def _files(paths: Iterable[Path] | None = None) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    return sorted(
        p for p in CORPUS_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(".")
    )


def build(
    paths: Iterable[Path] | None = None,
    *,
    rebuild: bool = False,
    client=None,
    verbose: bool = True,
) -> dict:
    """
    data/corpus -> LanceDB. Unchanged files are not re-read: docling takes
    30s+ per scan on an M1, which is not acceptable mid-demo.
    """
    from core.llm import Client

    _quiet()
    client = client or Client()
    manifest = {} if rebuild else _load_manifest()
    db = _db()
    converter = None

    files = _files(paths)
    todo = [f for f in files if manifest.get(f.name) != _fingerprint(f)]

    if verbose:
        print(f"corpus: {len(files)} files, {len(todo)} new or changed")

    stats = {"files": len(files), "converted": 0, "chunks": 0,
             "skipped": len(files) - len(todo), "seconds": 0.0}
    t0 = time.perf_counter()
    rows: list[dict] = []

    for f in todo:
        converter = converter or _converter()
        t = time.perf_counter()
        try:
            chunks = convert(f, converter)
        except SelfGenerated as exc:
            if verbose:
                print(f"  -- {f.name}: skipped ({exc})")
            continue
        except Exception as exc:                     # one file must not fail the run
            if verbose:
                print(f"  !! {f.name}: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            if verbose:
                print(f"  -- {f.name}: 0 chunks (empty or unreadable)")
            continue

        vecs = client.embed([c.text for c in chunks])
        for c, v in zip(chunks, vecs):
            rows.append({**asdict(c), "vector": v})

        manifest[f.name] = _fingerprint(f)
        stats["converted"] += 1
        stats["chunks"] += len(chunks)
        if verbose:
            pages = sorted({c.page for c in chunks if c.page})
            span = f"p.{pages[0]}-{pages[-1]}" if pages else "—"
            print(f"  ok {f.name:34} {len(chunks):3} chunks  {span:10} "
                  f"{time.perf_counter()-t:5.1f}s")

    if rows:
        names = _tables(db)
        if rebuild and TABLE in names:
            db.drop_table(TABLE)
            names = _tables(db)
        if TABLE in names:
            tbl = db.open_table(TABLE)
            ids = {r["chunk_id"] for r in rows}
            # drop the old chunks for a re-ingested file, else duplicates
            srcs = {r["source"] for r in rows}
            tbl.delete(" OR ".join(f"source = '{s}'" for s in sorted(srcs)))
            tbl.add(rows)
        else:
            db.create_table(TABLE, rows)

    _save_manifest(manifest)
    stats["seconds"] = time.perf_counter() - t0
    if verbose:
        print(f"\n{stats['converted']} files -> {stats['chunks']} chunks "
              f"in {stats['seconds']:.1f}s  (skipped {stats['skipped']})")
    return stats


def search(query: str, k: int = 5, *, client=None,
           source: str | None = None, sources: list[str] | None = None,
           min_score: float | None = None,
           per_source: int = 0) -> list[Hit]:
    """
    Semantic search. Every hit carries a citation.

    min_score: discard hits below this. Vector search always returns k
    results however poor the match - without a floor, "hi" retrieves the
    same passages as a real question. Default comes from models.yaml.
    """
    from core.llm import Client

    client = client or Client()
    db = _db()
    if TABLE not in _tables(db):
        raise RuntimeError("index is empty - run `python -m ingest.pipeline build` first")

    if min_score is None:
        min_score = float(client.reg.retrieval.get("min_score", 0.0))

    qv = client.embed(query)[0]
    # per_source spreads the budget across documents instead of letting one
    # file win every slot - needed when the question is about "all the
    # documents" rather than one fact.
    limit = k if not per_source else max(k, per_source * 40)
    if tags_in(query):
        limit = max(limit, 60)      # boosting cannot save a chunk never fetched
    q = db.open_table(TABLE).search(qv).limit(limit)
    if source:
        q = q.where(f"source = '{source}'")
    elif sources:
        joined = ", ".join(f"'{s}'" for s in sources)
        q = q.where(f"source IN ({joined})")

    hits: list[Hit] = []
    for r in q.to_list():
        dist = float(r.get("_distance", 0.0))
        hits.append(
            Hit(
                chunk=Chunk(
                    chunk_id=r["chunk_id"], source=r["source"], path=r["path"],
                    page=int(r["page"]), heading=r["heading"], kind=r["kind"],
                    text=r["text"],
                ),
                score=1.0 / (1.0 + dist),       # L2 -> 0..1, for display
            )
        )
    # Lexical rescue. A chunk that literally names the tag in the question is
    # relevant whatever the vector says, so it is boosted above the floor
    # rather than discarded by it.
    wanted = tags_in(query)
    if wanted:
        for h in hits:
            if any(_mentions(h.chunk.text, t) for t in wanted):
                h.score = min(1.0, h.score + TAG_BOOST)
        hits.sort(key=lambda h: -h.score)

    hits = [h for h in hits if h.score >= min_score]
    if per_source:
        seen: dict[str, int] = {}
        spread = []
        for h in hits:
            n = seen.get(h.chunk.source, 0)
            if n < per_source:
                seen[h.chunk.source] = n + 1
                spread.append(h)
        hits = spread[:k] if k else spread
    # Truncate back to k. Widening the candidate pool for the tag boost is an
    # internal detail; a caller that asked for k results must still get k, or
    # the whole pool lands in the prompt and buries the answer.
    return hits[:k] if k else hits


def context(query: str, k: int = 5, *, client=None,
            min_score: float | None = None, sources: list[str] | None = None,
            per_source: int = 0) -> tuple[str, list[Hit]]:
    """
    A block ready for the reason lane, plus the hits.
    Each passage is numbered [1] [2] so the model can cite it.

    Returns ("", []) when nothing relevant is found - the caller must then
    NOT use the grounded prompt, or the model will turn irrelevant passages
    into an answer.
    """
    hits = search(query, k, client=client, min_score=min_score,
                  sources=sources, per_source=per_source)
    blocks = [
        f"[{i}] {h.chunk.cite()}\n{h.chunk.text}"
        for i, h in enumerate(hits, 1)
    ]
    return "\n\n".join(blocks), hits


def status() -> dict:
    db = _db()
    if TABLE not in _tables(db):
        return {"indexed": False, "chunks": 0, "sources": []}
    tbl = db.open_table(TABLE)
    rows = tbl.search().limit(100000).to_list()
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    return {
        "indexed": True,
        "chunks": len(rows),
        "sources": sorted(by_src.items()),
        "dim": len(rows[0]["vector"]) if rows else 0,
    }


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str]) -> int:
    from core import airgap
    airgap.seal()
    _quiet()

    cmd = argv[0] if argv else "build"

    if cmd == "build":
        build(rebuild="--rebuild" in argv)
    elif cmd == "search":
        if len(argv) < 2:
            print('usage: search "query"')
            return 2
        t = time.perf_counter()
        hits = search(" ".join(argv[1:]))
        print(f"{len(hits)} hits in {time.perf_counter()-t:.2f}s\n")
        for h in hits:
            print(f"[{h.score:.3f}] {h.chunk.cite()}  ({h.chunk.kind})")
            print(f"        {h.chunk.text[:160].replace(chr(10),' ')}\n")
    elif cmd == "status":
        s = status()
        if not s["indexed"]:
            print("index is empty")
        else:
            print(f"{s['chunks']} chunks, dim={s['dim']}")
            for src, n in s["sources"]:
                print(f"  {n:4}  {src}")
    else:
        print(__doc__)
        return 2

    print("\n" + airgap.MONITOR.report())
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
