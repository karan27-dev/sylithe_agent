"""
Regression tests for three specific failures found by hand-testing against
the real index: Action Tracker missing the real table, Compare mixing two
unrelated bulletins, and Meeting Prep stating an arithmetic relation
backwards. Ground truth comes from the actual corpus files, most of it from
backend/data/corpus/maintenance_bulletin_MB-2026-17_REV2.md, a deliberately
revised bulletin created to have known, checkable differences from
maintenance_bulletin.md:
    min required thickness   10.4 mm -> 10.2 mm
    PSV-2041 reset due date  27-Sep-2026 -> 15-Oct-2026
    new action added         Verify calibration of LT-4102
    temperature limit        65 degC -> 60 degC
    (escalation threshold, 10.8 mm, is UNCHANGED in both - a correct
    comparison must not report it as a difference)

Two layers:
  1. Unit tests directly against tools/actions.py, tools/compare_docs.py and
     tools/verify.py - fast, deterministic, no model call. These are the
     real regression guard: if these fail, the fix itself is broken.
  2. End-to-end tasks through Agent.run(), reusing bench.run_benchmark's own
     Task/run_one - slower (real model calls) and can vary in wording, but
     they are what actually shipped and are worth running.

    python -m bench.run_fixes_test
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_pass = 0
_fail = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. unit tests - no model call
# ---------------------------------------------------------------------------

def unit_tests() -> None:
    print("\n== unit tests (tools/actions.py, compare_docs.py, verify.py) ==")

    from tools import actions as actions_tool
    from tools import compare_docs as compare_tool
    from tools import verify as verify_tool

    # -- action tracker ------------------------------------------------
    tk_rows = actions_tool.extract_actions("TK-4102")
    check("action tracker: finds TK-4102 rows", len(tk_rows) > 0,
          f"got {len(tk_rows)} rows")
    check("action tracker: includes the new REV2 action (LT-4102)",
          any("LT-4102" in r.action for r in tk_rows))
    check("action tracker: correct due date for PSV-2041 reset (REV2)",
          any(r.due == "15-Oct-2026" for r in tk_rows
              if "PSV-2041" in r.action and "REV2" in r.source))
    check("action tracker: no PSV-201 contamination from the wrong bulletin",
          not any("PSV-201" in r.action or "E-204" in r.action for r in tk_rows))

    psv_rows = actions_tool.extract_actions("PSV-201")
    check("action tracker: PSV-201 query returns MB-2026-22's own rows",
          len(psv_rows) == 3 and all("MB-2026-22" in r.source for r in psv_rows))
    check("action tracker: PSV-201 query has no TK-4102 bleed-through",
          not any("PSV-2041" in r.action or "LT-4102" in r.action for r in psv_rows))

    ghost_rows = actions_tool.extract_actions("XYZ-999")
    check("action tracker: never invents rows for a tag that does not exist",
          ghost_rows == [])

    # -- compare documents ----------------------------------------------
    q = ("Compare Maintenance Bulletin MB-2026-17 with its REV 2 - what "
         "values or instructions changed?")
    docs = compare_tool.find_documents(q)
    check("compare: finds exactly 2 documents", len(docs) == 2,
          f"got {[d.source for d in docs]}")
    sources = {d.source for d in docs}
    check("compare: picks the REV2 file, not the unrelated MB-2026-22",
          "maintenance_bulletin_MB-2026-17_REV2.md" in sources
          and "Maintenance Bulletin MB-2026-22.md" not in sources,
          f"got {sources}")

    if len(docs) == 2:
        result = compare_tool.diff_documents(*docs)
        changed_text = " ".join(f"{c.old} {c.new}" for c in result.changes)
        for expected in ("10.2", "15-Oct-2026", "LT-4102", "60 degC"):
            check(f"compare: reports the real change containing {expected!r}",
                  expected in changed_text)
        check("compare: does not report the unchanged 10.8 mm line as a diff",
              "10.8" not in changed_text)
        check("compare: no line from the unrelated MB-2026-22 leaks in",
              "PSV-201" not in changed_text and "E-204" not in changed_text)

    no_match = compare_tool.find_documents("compare the weather forecast with yesterday")
    check("compare: does not force a match when nothing scores high enough",
          len(no_match) < 2, f"got {[d.source for d in no_match]}")

    # -- claim verifier ---------------------------------------------------
    bad = ("TK-4102 operating level is currently at 76%, which exceeds the "
           "82% limit in Safety Circular SC-2026-09 [1].")
    passages = ["TK-4102 level 76%, temp 61 degC. Holding.",
                "Do not raise TK-4102 operating level above 82% until the "
                "valve is reset. Safety Circular SC-2026-09"]
    issues = verify_tool.verify_claims(bad, passages)
    check("verify: catches '76% exceeds 82%' as backwards", len(issues) == 1,
          f"got {len(issues)} issue(s)")

    # Regression: found in the live e2e run below, not by inspection. The
    # citation sits BETWEEN the number and the relation word, exactly as a
    # real grounded answer wrote it - the first version of _check_relation
    # matched a hand-built test sentence with the citation at the end and
    # missed this one entirely.
    bad_mid_citation = ("TK-4102 level is 76% [1], which exceeds the 82% "
                        "limit in Safety Circular SC-2026-09 [3].")
    issues_mid = verify_tool.verify_claims(bad_mid_citation, passages)
    check("verify: catches the relation even with a citation marker "
          "between the number and the comma",
          len(issues_mid) == 1, f"got {len(issues_mid)} issue(s)")

    good = ("PSV-2041 was found at 12.5 barg, below the 14.0 barg required "
            "by SOP-114 [1].")
    good_passages = ["PSV-2041 inspected. Set pressure observed 12.5 barg. "
                     "SOP-114 requires 14.0 barg."]
    issues2 = verify_tool.verify_claims(good, good_passages)
    check("verify: does not flag a correct, well-sourced relation",
          issues2 == [], f"got {[i.reason for i in issues2]}")

    cited = "A non-conformance was raised as NCR-2026-0088 [2]."
    issues3 = verify_tool.verify_claims(cited, ["Raised NCR-2026-0088."])
    check("verify: citation markers like [2] are not mistaken for claimed figures",
          issues3 == [], f"got {[i.reason for i in issues3]}")

    # -- history relevance filter -----------------------------------------
    from agents.workbench import select_relevant_history

    # UNRELATED case: reproduces the actual chats.json scenario that made
    # the API and browser tests disagree - two unrelated prior turns ahead
    # of a new, unrelated TK-4102 question.
    unrelated_history = [
        {"role": "user", "content": "Write and run a Python script to "
         "calculate the volume of a cylindrical storage tank"},
        {"role": "assistant", "content": "```python\n# volume calc\n```"},
        {"role": "user", "content": "Write a script that downloads the "
         "API 653 standard from the internet"},
        {"role": "assistant", "content": "Network access is disabled..."},
    ]
    filtered = select_relevant_history(
        "List all pending actions for TK-4102 with owner and due date",
        unrelated_history)
    check("history filter: drops unrelated prior turns for a new topic",
          filtered == [], f"kept {len(filtered)} message(s)")

    # RELEVANT case: a follow-up that only makes sense with the immediately
    # preceding turn kept ("the above" has no meaning otherwise).
    relevant_history = [
        {"role": "user", "content": "What deviation was found on TK-4102?"},
        {"role": "assistant", "content": "TK-4102 showed 11.2 mm against a "
         "nominal of 12.0 mm [1]."},
    ]
    filtered2 = select_relevant_history(
        "Convert the above report into Excel", relevant_history)
    check("history filter: keeps the immediately preceding turn for a follow-up",
          filtered2 == relevant_history)

    # RELEVANT case, older turn: same tag mentioned again after unrelated
    # turns in between - the older TK-4102 turn should still be pulled back
    # in, not just the most recent one.
    mixed_history = [
        {"role": "user", "content": "What deviation was found on TK-4102?"},
        {"role": "assistant", "content": "TK-4102 showed 11.2 mm [1]."},
        {"role": "user", "content": "What can you do?"},
        {"role": "assistant", "content": "I can answer questions about "
         "indexed plant documents."},
    ]
    filtered3 = select_relevant_history(
        "What is the remaining life for TK-4102?", mixed_history)
    check("history filter: pulls back an older same-tag turn across "
          "unrelated turns in between",
          mixed_history[0] in filtered3 and mixed_history[1] in filtered3,
          f"kept {filtered3}")


# ---------------------------------------------------------------------------
# 2. end-to-end through the real agent (slower, real model calls)
# ---------------------------------------------------------------------------

def e2e_tasks() -> None:
    print("\n== end-to-end through Agent.run() (real model calls) ==")
    from core import airgap
    airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from agents.workbench import Agent
    from bench.run_benchmark import Task, run_one

    tasks = [
        Task("e2e-actions", "action tracker fix",
             "List all pending actions for TK-4102 with owner and due date",
             must_contain=["LT-4102", "15-Oct-2026"],
             must_not_contain=["PSV-201", "E-204"]),
        Task("e2e-compare", "compare documents fix",
             "Compare Maintenance Bulletin MB-2026-17 with its REV 2 - what "
             "values or instructions changed?",
             must_contain=["10.2", "60"],
             must_not_contain=["PSV-201", "25.0 barg"]),
        Task("e2e-meeting-prep", "meeting prep verify (informational)",
             "Prepare notes for a shift handover meeting on TK-4102 and "
             "PSV-2041 - key issues, unresolved items, and questions to raise",
             must_contain=[]),
    ]
    agent = Agent()
    for t in tasks:
        r = run_one(agent, t)
        mark = "PASS" if r["passed"] else "FAIL" if t.must_contain else "INFO"
        if mark == "FAIL":
            globals()["_fail"] += 1
        elif mark == "PASS":
            globals()["_pass"] += 1
        print(f"  {mark:5} {t.id:20} {r['seconds']:5.1f}s")
        for f in r["fails"]:
            print(f"        ! {f}")
        if t.id == "e2e-meeting-prep":
            flagged = "Needs review" in r["answer"]
            print(f"        (needs-review marker present: {flagged})")
        print(f"        answer: {r['answer'][:180]!r}")


def main() -> int:
    t0 = time.perf_counter()
    unit_tests()
    if "--unit-only" not in sys.argv:
        e2e_tasks()
    print(f"\n{_pass}/{_pass + _fail} checks passed in {time.perf_counter()-t0:.1f}s")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
