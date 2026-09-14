"""
Score the workbench on the five generated data sets, as a percentage.

Each set is indexed ALONE before its questions run. Mixing all five would let
an answer about T-301 be satisfied by a passage from set 5, which would measure
nothing except how similar the numbers are.

Ground truth is arithmetic done before the documents were written, not what the
system said last week. That distinction has already mattered here: a P&ID
expectation once encoded a spurious graph edge as "correct", and fixing the
graph made the benchmark call the right answer wrong.

    python -m bench.run_datasets            # all five
    python -m bench.run_datasets --set set-3
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DATASETS = _ROOT / "data" / "datasets"


def _reset_index() -> None:
    """A set must be scored on its own documents only."""
    from ingest import pipeline
    for p in (pipeline.INDEX_DIR, pipeline.MANIFEST):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()


def _index(folder: Path, client) -> int:
    from ingest import pipeline
    docs = [f for f in sorted(folder.iterdir())
            if f.suffix.lower() in pipeline.SUPPORTED
            and not f.name.lower().startswith("pid-")]
    stats = pipeline.build(docs, client=client, rebuild=True, verbose=False)
    return stats.get("chunks", 0)


def _drawing(folder: Path) -> str | None:
    for f in folder.iterdir():
        if f.name.lower().startswith("pid-"):
            return str(f)
    return None


NEGATORS = ("not ", "n't ", "no ", "never ", "fails", "exceeds", "below ",
            "does not", "non-compliant", "noncompliant", "outside ")


def _asserts(text: str, word: str) -> bool:
    """
    Did the answer actually CLAIM this word, rather than deny it?

    Looks at the words immediately before each standalone occurrence. Crude,
    but it is the difference between "compliant" and "is not compliant", and
    scoring those the same makes the benchmark worse than useless.
    """
    import re as _re
    for m in _re.finditer(rf"\b{_re.escape(word.lower())}\b", text):
        before = text[max(0, m.start() - 60):m.start()]
        if not any(n in before for n in NEGATORS):
            return True
    return False


def ask(agent, question: str, drawing: str | None) -> dict:
    answer, code_out, files = "", "", []
    for ev in agent.run(question, drawing=drawing):
        t = ev.get("type")
        if t == "token":
            answer += ev["text"]
        elif t == "code_result":
            code_out += ev.get("stdout", "")
        elif t == "file":
            files.append(ev)
    return {"answer": answer, "text": answer + "\n" + code_out, "files": files}


def score_set(agent, meta: dict, client) -> dict:
    folder = Path(meta["folder"])
    _reset_index()
    chunks = _index(folder, client)
    drawing = _drawing(folder)

    rows, passed = [], 0
    for q in meta["questions"]:
        t0 = time.perf_counter()
        r = ask(agent, q["ask"], drawing)
        low = r["text"].lower()
        missing = [e for e in q["expect"] if e.lower() not in low]
        # A reject word must stand alone AND not be negated. Plain substring
        # matching failed two correct answers: "is not compliant" contains
        # "compliant", and an answer explaining that 78% exceeds the limit
        # still contains "within". The check was wrong, not the answer.
        wrong = [x for x in q.get("reject", []) if _asserts(low, x)]
        ok = not missing and not wrong
        passed += ok
        rows.append({"ask": q["ask"], "passed": ok, "missing": missing,
                     "wrongly_said": wrong, "why": q.get("why", ""),
                     "seconds": round(time.perf_counter() - t0, 1),
                     "answer": r["answer"].strip()[:180]})
    pct = 100.0 * passed / max(1, len(rows))
    return {"id": meta["id"], "level": meta["level"], "title": meta["title"],
            "chunks": chunks, "passed": passed, "total": len(rows),
            "percent": round(pct, 1), "questions": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="only")
    a = ap.parse_args()

    from core import airgap
    monitor = airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from core.llm import Client
    from agents.workbench import Agent

    manifest = json.loads((DATASETS / "manifest.json").read_text())
    if a.only:
        manifest = [m for m in manifest if m["id"] == a.only]

    client = Client()
    agent = Agent(client)
    results, t0 = [], time.perf_counter()

    for meta in manifest:
        print(f"\n=== {meta['id']}  [{meta['level']}]  {meta['title']}")
        r = score_set(agent, meta, client)
        results.append(r)
        for q in r["questions"]:
            mark = "PASS" if q["passed"] else "FAIL"
            print(f"   {mark}  {q['ask'][:64]:66} {q['seconds']:5.1f}s")
            if not q["passed"]:
                if q["missing"]:
                    print(f"         missing {q['missing']}")
                if q["wrongly_said"]:
                    print(f"         wrongly said {q['wrongly_said']}")
                if q["why"]:
                    print(f"         expected: {q['why'][:88]}")
        print(f"   -> {r['passed']}/{r['total']}  {r['percent']:.0f}%")

    tot_p = sum(r["passed"] for r in results)
    tot_q = sum(r["total"] for r in results)
    overall = 100.0 * tot_p / max(1, tot_q)
    by_level: dict[str, list] = {}
    for r in results:
        by_level.setdefault(r["level"], []).append(r)

    print("\n" + "=" * 70)
    print(f"{'set':8} {'level':9} {'score':>8}  title")
    for r in results:
        print(f"{r['id']:8} {r['level']:9} {r['percent']:7.0f}%  {r['title'][:44]}")
    print("-" * 70)
    for lvl in ("easy", "medium", "complex"):
        rs = by_level.get(lvl)
        if rs:
            p = sum(x["passed"] for x in rs)
            t = sum(x["total"] for x in rs)
            print(f"{lvl:18} {100.0*p/t:7.0f}%   ({p}/{t})")
    print("-" * 70)
    print(f"{'OVERALL':18} {overall:7.1f}%   ({tot_p}/{tot_q})")
    s = monitor.summary()
    print(f"{'external calls':18} {s['external_calls']:8}")
    print(f"{'elapsed':18} {time.perf_counter()-t0:7.0f}s")

    out = _ROOT / "bench" / "dataset_results.json"
    out.write_text(json.dumps(
        {"overall_percent": round(overall, 1), "passed": tot_p, "total": tot_q,
         "external_calls": s["external_calls"], "sets": results}, indent=2))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
