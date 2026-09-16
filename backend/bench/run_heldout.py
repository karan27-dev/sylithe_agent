"""
Score the held-out NHT test pack against its own answer key.

The key was written before anything was run and its tags are disjoint from the
demo corpus (V-2405, E-2404, PSV-2418 - not TK-4102 or V-7101), so it shows
whether the system has quietly overfitted to the documents it was built on.

Expected values are transcribed from ANSWER-KEY.md, not from any output.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# id, question, must_contain, must_not_contain, note
Q = [
    ("A1", "What is the corrosion rate of V-2405, and what is its remaining life?",
     ["0.5", "6.4"], [], "governing CML-02: (14.20-12.70)/3 = 0.500; (12.70-9.50)/0.5 = 6.40"),
    ("A2", "When is the next inspection of V-2405 due?",
     ["3.2"], [], "lesser of half remaining life (3.20 yr) or 10 yr"),

    ("B1", "Is PSV-2418 acceptable to return to service?",
     ["23.28", "24.72", "24.5"], ["non-conforming"], "band must be written out; 24.50 is INSIDE"),
    ("B2", "Is PSV-2422 acceptable to return to service?",
     ["11.64", "12.36", "ncr-2026-044"], ["dev-2026-017"],
     "12.60 outside band; DEV covers E-2404 only"),

    ("C1", "E-2404 shell measured 8.20 mm against a t-min of 8.50 mm. "
           "Can it stay in service?",
     ["dev-2026-017", "7.8", "30-nov-2026"], [],
     "all four: deviation, 7.80 floor, expiry, a condition"),
    ("C2", "Does DEV-2026-017 apply to PSV-2422?",
     ["no"], [], "deviation is never applied by analogy"),

    ("D1", "What is the oxygen acceptance criterion for nitrogen purging on "
           "the NHT reactor loop?",
     # The key requires BOTH halves - the Rev 3 value and Rev 2 named as
     # superseded. A first version of this test banned "8.0 vol", which marked
     # a correct answer wrong: naming Rev 2's dead figure is required, not
     # forbidden. The fail mode is presenting the two as equally valid.
     ["5.0", "rev 3", "supersed"], [], "Rev 3 value AND Rev 2 named superseded"),
    ("D2", "How long must the nitrogen purge run, and how many blind points "
           "are required?",
     ["4 hour", "6 blind"], [], "Rev 3: 4 hours, BL-01..BL-06"),

    ("E1", "What is the shell thickness of TK-2410?",
     ["not"], ["12.70", "8.20", "14.20", "16.00"],
     "CRITICAL: never surveyed; any thickness number is the TK-9999 bug"),
    ("E2", "What is the design pressure of V-9999?",
     ["not"], ["18.5"], "CRITICAL: must not return V-2405's 18.5 barg"),

    ("F1", "Why is P-2402B out of service?",
     ["vibration", "7.8"], [], "image-only PDF - fails here mean OCR is not running"),
    ("F2", "What was the TK-2410 level on the night of 9 March?",
     ["62"], [], "TK-2410 has a level on record but no thickness - pairs with E1"),

    ("G2", "Does the nameplate design pressure match the inspection report?",
     ["18.5"], [], "one fact from vision, one from retrieval"),

    ("H1", "What must be closed to isolate V-2405 for entry?",
     ["hv-2431", "hv-2432"], ["close psv-2418", "closing psv-2418",
                              "psv-2418 and", "and psv-2418"],
     "AUTOMATIC FAIL if PSV-2418 is named among valves to close"),
]


def main() -> int:
    from core import airgap
    monitor = airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from agents.workbench import Agent

    agent = Agent()
    rows = []
    print(f"{'id':4} {'result':7} {'sec':>6}  note")
    print("-" * 78)
    for qid, question, must, mustnt, note in Q:
        t0 = time.perf_counter()
        answer, code_out, files = "", "", []
        for ev in agent.run(question):
            t = ev.get("type")
            if t == "token":
                answer += ev["text"]
            elif t == "code_result":
                code_out += ev.get("stdout", "")
            elif t == "file":
                files.append(ev)
        hay = (answer + "\n" + code_out).lower()
        fails = []
        miss = [m for m in must if m.lower() not in hay]
        if miss:
            fails.append(f"missing {miss}")
        bad = [m for m in mustnt if m.lower() in hay]
        if bad:
            fails.append(f"SAID {bad}")
        dt = time.perf_counter() - t0
        rows.append({"id": qid, "question": question, "passed": not fails,
                     "fails": fails, "seconds": round(dt, 1),
                     "answer": answer.strip()})
        print(f"{qid:4} {'PASS' if not fails else 'FAIL':7} {dt:6.1f}  {note}")
        for f in fails:
            print(f"       ! {f}")

    ok = sum(1 for r in rows if r["passed"])
    s = monitor.summary()
    print("-" * 78)
    print(f"{ok}/{len(rows)} passed")
    print(f"EXTERNAL CALLS: {s['external_calls']}   air-gapped: {s['airgapped']}")
    (_ROOT / "bench" / "heldout_results.json").write_text(
        json.dumps({"passed": ok, "total": len(rows), "results": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
