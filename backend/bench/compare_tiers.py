"""
Run the same questions on two tiers and put the answers side by side.

    python -m bench.compare_tiers --a tier-S --b tier-R

Why this rather than a demo where the bigger model simply looks better: a
viewer cannot tell a better answer from a more confident one. Both columns are
scored against expectations written down beforehand, so the comparison says
which answers were RIGHT rather than which read more smoothly.

Expectations come from the held-out Pack B key, derived from the constants
that generated those documents.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# id, question, must_contain, must_not_contain
Q = [
    ("B1", "What is the corrosion rate of V-3302, and what is its remaining life?",
     ["0.5", "2.2"], []),
    ("B2", "When is the next inspection of V-3302 due?", ["1.1"], []),
    ("B3", "Is PSV-3312 acceptable to return to service?",
     ["62.08", "65.92"], ["non-conforming"]),
    ("B4", "Is PSV-3318 acceptable to return to service?",
     ["17.46", "18.54", "ncr-2027-066"], []),
    ("B5", "E-3304 shell measured 8.75 mm against a t-min of 9.20 mm. "
           "Can it stay in service?",
     ["dev-2027-009", "8.4", "31-aug-2027"], []),
    ("B6", "Does DEV-2027-009 apply to PSV-3318?", ["no"], []),
    ("B7", "What is the oxygen acceptance criterion for nitrogen purging "
           "on Unit 33?", ["3.5", "supersed"], []),
    ("B8", "How long must the nitrogen purge run, and how many blind points "
           "are required?", ["6 hour", "8 blind"], []),
    ("B9", "What is the shell thickness of TK-3340?",
     ["not"], ["16.60", "18.60", "8.75"]),
    ("B10", "What is the design pressure of V-8888?", ["not"], ["62"]),
    ("B11", "Why is P-3320B out of service?", ["vibration", "9.4"], []),
    ("B12", "What was the TK-3340 level on the night shift?", ["48"], []),
    ("B14", "What must be closed to isolate V-3302 for entry?",
     ["hv-3351", "hv-3352"], ["close psv-3312", "closing psv-3312"]),
]


def run_tier(tier: str, corpus: list[str]) -> list[dict]:
    from core.llm import Client
    from agents.workbench import Agent

    client = Client()
    client.profile_name = tier
    agent = Agent(client)
    out = []
    for qid, q, must, mustnt in Q:
        t0 = time.perf_counter()
        ans = ""
        try:
            for ev in agent.run(q, corpus=corpus):
                if ev.get("type") == "token":
                    ans += ev["text"]
            err = ""
        except Exception as exc:                   # a sealed tier refuses here
            err = f"{type(exc).__name__}: {exc}"[:90]
        dt = time.perf_counter() - t0
        hay = ans.lower()
        fails = [m for m in must if m.lower() not in hay]
        bad = [m for m in mustnt if m.lower() in hay]
        out.append({"id": qid, "seconds": round(dt, 1),
                    "passed": not (fails or bad or err),
                    "error": err, "missing": fails, "said": bad,
                    "answer": ans.strip()[:400]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="tier-S")
    ap.add_argument("--b", default="tier-R")
    ap.add_argument("--corpus-prefix", default="B",
                    help="only index files starting with this (the pack)")
    a = ap.parse_args()

    import sys
    sys.path.insert(0, str(_ROOT))
    from ingest import pipeline
    from ingest.pipeline import _quiet
    _quiet()

    corpus = sorted(f.name for f in pipeline.CORPUS_DIR.iterdir()
                    if f.name.startswith(a.corpus_prefix))
    if not corpus:
        print(f"no files starting with {a.corpus_prefix!r} in "
              f"{pipeline.CORPUS_DIR}")
        return 1
    print(f"{len(corpus)} documents, {len(Q)} questions, "
          f"{a.a} vs {a.b}\n")

    res = {t: run_tier(t, corpus) for t in (a.a, a.b)}

    print(f"{'id':5} {a.a:>22} {a.b:>22}")
    print("-" * 52)
    for i, (qid, *_rest) in enumerate(Q):
        cells = []
        for t in (a.a, a.b):
            r = res[t][i]
            mark = "PASS" if r["passed"] else ("ERR" if r["error"] else "FAIL")
            cells.append(f"{mark:>5} {r['seconds']:>6.1f}s")
        print(f"{qid:5} {cells[0]:>22} {cells[1]:>22}")
    print("-" * 52)
    for t in (a.a, a.b):
        ok = sum(1 for r in res[t] if r["passed"])
        secs = sum(r["seconds"] for r in res[t])
        print(f"{t:22} {ok}/{len(Q)} correct, {secs:.0f}s total, "
              f"{secs / len(Q):.0f}s per answer")

    (_ROOT / "bench" / "tier_comparison.json").write_text(
        json.dumps(res, indent=1))
    print(f"\n-> bench/tier_comparison.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
