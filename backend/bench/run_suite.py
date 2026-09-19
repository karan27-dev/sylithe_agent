"""
Full workbench benchmark - 120 questions, five difficulty tiers.

Scored against ground truth derived from the generator constants
(see bench/suite_questions.py), not against how an answer reads. Two scores
are reported because they answer different questions:

  STRICT   every required fact present AND no forbidden fact present.
           This is the headline number.
  PARTIAL  fraction of required facts present, averaged. Shows whether a
           failure was "missed one number out of three" or "said nothing".

A forbidden-fact hit on a tier-L5 question, or on any question whose note
starts with CRITICAL, is counted separately as a SAFETY failure. Those are
not ordinary misses: they are the answers that would send a person to the
wrong valve, so a run with zero strict failures but one safety failure is
worse than the reverse.

    python -m bench.run_suite                # everything
    python -m bench.run_suite --limit 5      # smoke test
    python -m bench.run_suite --tier L5      # one tier
    python -m bench.run_suite --resume       # continue an interrupted run
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "bench" / "suite_results.json"


def _hit(needle, hay: str) -> bool:
    """A needle is a string, or a tuple meaning any-one-of."""
    if isinstance(needle, (tuple, list)):
        return any(str(n).lower() in hay for n in needle)
    return str(needle).lower() in hay


def _label(needle) -> str:
    if isinstance(needle, (tuple, list)):
        return " | ".join(str(n) for n in needle)
    return str(needle)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tier", default="")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    from core import airgap
    monitor = airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from agents.workbench import Agent
    from bench.suite_questions import Q

    todo = list(Q)
    if a.tier:
        todo = [x for x in todo if x[1] == a.tier.upper()]
    if a.limit:
        todo = todo[: a.limit]

    done: dict[str, dict] = {}
    if a.resume and OUT.exists():
        prev = json.loads(OUT.read_text())
        done = {r["id"]: r for r in prev.get("results", [])}
        print(f"resuming - {len(done)} question(s) already scored")

    agent = Agent()
    rows: list[dict] = []
    t_all = time.perf_counter()

    print(f"{'id':5} {'tier':5} {'res':6} {'sec':>6}  question")
    print("-" * 100)

    for i, (qid, tier, cat, question, must, must_not, note) in enumerate(todo, 1):
        if qid in done:
            rows.append(done[qid])
            continue

        t0 = time.perf_counter()
        answer, code_out = "", ""
        try:
            for ev in agent.run(question):
                t = ev.get("type")
                if t == "token":
                    answer += ev["text"]
                elif t == "code_result":
                    code_out += ev.get("stdout", "")
        except Exception as exc:                       # a crash is a failure, not a stop
            answer = f"__ERROR__ {type(exc).__name__}: {exc}"
        dt = time.perf_counter() - t0

        hay = (answer + "\n" + code_out).lower()
        matched = [m for m in must if _hit(m, hay)]
        missing = [_label(m) for m in must if not _hit(m, hay)]
        violated = [_label(m) for m in must_not if _hit(m, hay)]

        partial = len(matched) / len(must) if must else 1.0
        passed = not missing and not violated
        critical = bool(violated) and (tier == "L5" or note.startswith("CRITICAL"))

        row = {
            "id": qid, "tier": tier, "category": cat, "question": question,
            "passed": passed, "partial": round(partial, 3),
            "missing": missing, "violated": violated, "critical": critical,
            "seconds": round(dt, 1), "answer": answer.strip()[:1500], "note": note,
        }
        rows.append(row)

        flag = "PASS" if passed else ("SAFETY" if critical else "FAIL")
        print(f"{qid:5} {tier:5} {flag:6} {dt:6.1f}  {question[:66]}")
        if missing:
            print(f"                     ! missing: {missing}")
        if violated:
            print(f"                     ! SAID: {violated}")

        # written every question - an 80 minute run must survive a kill
        OUT.write_text(json.dumps(_summary(rows, monitor, t_all), indent=1))

    payload = _summary(rows, monitor, t_all)
    OUT.write_text(json.dumps(payload, indent=1))
    _report(payload)
    return 0


def _summary(rows: list[dict], monitor, t_all: float) -> dict:
    n = len(rows)
    strict = sum(1 for r in rows if r["passed"])
    partial = sum(r["partial"] for r in rows) / n if n else 0.0
    by_tier: dict[str, dict] = {}
    by_cat: dict[str, dict] = {}
    for r in rows:
        for key, bucket in (("tier", by_tier), ("category", by_cat)):
            d = bucket.setdefault(r[key], {"n": 0, "pass": 0, "partial": 0.0})
            d["n"] += 1
            d["pass"] += int(r["passed"])
            d["partial"] += r["partial"]
    for bucket in (by_tier, by_cat):
        for d in bucket.values():
            d["pct"] = round(100 * d["pass"] / d["n"], 1)
            d["partial_pct"] = round(100 * d["partial"] / d["n"], 1)
            d.pop("partial")
    s = monitor.summary()
    return {
        "total": n,
        "strict_pass": strict,
        "strict_pct": round(100 * strict / n, 1) if n else 0.0,
        "partial_pct": round(100 * partial, 1),
        "safety_failures": sum(1 for r in rows if r["critical"]),
        "external_calls": s["external_calls"],
        "airgapped": s["airgapped"],
        "elapsed_s": round(time.perf_counter() - t_all, 1),
        "by_tier": dict(sorted(by_tier.items())),
        "by_category": dict(sorted(by_cat.items())),
        "results": rows,
    }


def _report(p: dict) -> None:
    print("-" * 100)
    print(f"STRICT   {p['strict_pass']}/{p['total']} = {p['strict_pct']}%")
    print(f"PARTIAL  {p['partial_pct']}%")
    print(f"SAFETY   {p['safety_failures']} failure(s)")
    print(f"EXTERNAL CALLS: {p['external_calls']}   air-gapped: {p['airgapped']}")
    print()
    print(f"{'tier':6} {'n':>4} {'strict':>8} {'partial':>8}")
    for k, d in p["by_tier"].items():
        print(f"{k:6} {d['n']:4} {d['pct']:7}% {d['partial_pct']:7}%")
    print()
    print(f"{'category':14} {'n':>4} {'strict':>8} {'partial':>8}")
    for k, d in p["by_category"].items():
        print(f"{k:14} {d['n']:4} {d['pct']:7}% {d['partial_pct']:7}%")


if __name__ == "__main__":
    raise SystemExit(main())
