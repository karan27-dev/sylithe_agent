"""
End-to-end agent benchmark on ten previously unseen plant documents.

Nothing in the pipeline is changed to run this. The documents are ingested the
normal way and the questions go through the ordinary agent, exactly as a user
would ask them.

Ground truth, derived from the documents and written down BEFORE running:

  V-7101 Course-1  13.1 mm (2021) -> 11.6 mm (2026), interval 5.0 yr
                   rate  = 1.5 / 5.0            = 0.30 mm/yr
                   life  = (11.6 - 11.0) / 0.30 = 2.0 years to t-min 11.0
  PSV-7301  as-found 18.2 vs specified 20.0 -> -9%, outside +/-3%,
            BUT MOC-2026-044 accepts >= 18.0 barg for PSV-7301 until
            30-Jun-2027 -> acceptable today, not after that date
  PSV-7302  as-found 15.4 vs 20.0 -> outside band, NO deviation covers it
            -> non-conforming, NCR-2026-0412 open, WO-2026-9104, H-7-18
  PSV-7303  Class-B, 15.8 vs 16.0 -> -1.25%, within band -> conforming.
            Under the SUPERSEDED Rev 2 the Class-A floor was 16.0; a system
            that reads Rev 2 reaches a different verdict on Class-A devices.
  PSV-7304  20.4 vs 20.0 -> +2%, within band -> conforming
  E-7204    referenced in the line list, the HAZOP and a work order, but NEVER
            surveyed. Any thickness number for it is invented.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

Q = [
    # --- single document, exact retrieval -------------------------------
    ("thickness", "What is the Course-1 shell thickness of V-7101 in the "
     "2026 survey?", ["11.6"], []),
    ("design-press", "What is the design pressure of V-7101?", ["21.0"], []),
    ("line-material", 'What material is line 12"-P-7105-A1A?',
     ["a106"], []),

    # --- arithmetic across two readings ---------------------------------
    ("corrosion-rate", "Using both UT surveys, what is the corrosion rate of "
     "V-7101 Course-1 in mm per year?", ["0.3"], []),
    ("remaining-life", "At that corrosion rate, how many years until V-7101 "
     "Course-1 reaches t-min?", ["2"], []),

    # --- cross-document judgement ---------------------------------------
    ("psv-7304", "Does the as-found set pressure of PSV-7304 conform to the "
     "current procedure?", ["20.4"], ["non-conforming", "fails"]),
    ("psv-7302", "Is PSV-7302 acceptable? Cite the NCR if there is one.",
     ["15.4", "ncr-2026-0412"], []),
    ("psv-7301-moc", "PSV-7301 was found at 18.2 barg against a specified "
     "20.0 barg. Is that acceptable right now?",
     ["moc-2026-044", "18.0"], []),
    ("psv-7301-expiry", "Until what date is the PSV-7301 deviation valid?",
     ["30-jun-2027"], []),
    ("moc-scope", "Does MOC-2026-044 also cover PSV-7302?",
     ["no"], []),

    # --- the superseded-revision trap ------------------------------------
    ("current-rev", "Which revision of SOP-7201 must I use for an assessment "
     "dated today, and what is the Class-A minimum set pressure?",
     ["rev 3", "20.0"], []),
    ("rev2-status", "Is SOP-7201 Rev 2 still valid?",
     ["superseded"], []),

    # --- refusal ----------------------------------------------------------
    ("refuse-e7204", "What is the measured shell thickness of E-7204?",
     ["not"], ["11.6", "12.7", "13.2"]),
    ("why-no-e7204", "Why is there no thickness data for E-7204?",
     ["scaffold"], []),

    # --- traceability chains ---------------------------------------------
    ("ncr-to-wo", "Which work order covers NCR-2026-0412?",
     ["wo-2026-9104"], []),
    ("hazop-owner", "Who owns HAZOP action H-7-18 and when is it due?",
     ["menon", "2026-10-15"], []),
    ("open-ncrs", "List the NCRs that are still open.",
     ["ncr-2026-0412", "ncr-2026-0408"], ["ncr-2025-0377"]),

    # --- deliverable ------------------------------------------------------
    ("deliverable", "Produce an XLSX summarising every PSV on CDU-7 with its "
     "specified and as-found set pressure and whether it conforms.",
     [], [], "xlsx"),
]


def main() -> int:
    from core import airgap
    monitor = airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from agents.workbench import Agent

    agent = Agent()
    rows = []
    print(f"{'question':18} {'result':7} {'sec':>6}")
    print("-" * 52)
    for item in Q:
        qid, question, must, mustnt = item[:4]
        want_file = item[4] if len(item) > 4 else None
        t0 = time.perf_counter()
        answer, files, code_out = "", [], ""
        for ev in agent.run(question):
            if ev.get("type") == "token":
                answer += ev["text"]
            elif ev.get("type") == "file":
                files.append(ev)
            elif ev.get("type") == "code_result":
                code_out += ev.get("stdout", "")
        hay = (answer + "\n" + code_out).lower()
        fails = []
        miss = [m for m in must if m.lower() not in hay]
        if miss:
            fails.append(f"missing {miss}")
        bad = [m for m in mustnt if m.lower() in hay]
        if bad:
            fails.append(f"should not say {bad}")
        if want_file and not any(f["kind"] == want_file for f in files):
            fails.append(f"no {want_file}")
        dt = time.perf_counter() - t0
        rows.append({"id": qid, "question": question, "passed": not fails,
                     "fails": fails, "seconds": round(dt, 1),
                     "answer": answer.strip()[:300]})
        print(f"{qid:18} {'PASS' if not fails else 'FAIL':7} {dt:6.1f}")
        for f in fails:
            print(f"    ! {f}")

    ok = sum(1 for r in rows if r["passed"])
    s = monitor.summary()
    print("-" * 52)
    print(f"{ok}/{len(rows)} passed in {sum(r['seconds'] for r in rows):.0f}s")
    print(f"EXTERNAL CALLS: {s['external_calls']}   air-gapped: {s['airgapped']}")
    (_ROOT / "bench" / "industry_results.json").write_text(
        json.dumps({"passed": ok, "total": len(rows), "results": rows},
                   indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
