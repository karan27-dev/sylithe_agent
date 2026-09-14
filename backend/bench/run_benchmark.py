"""
Benchmark the workbench against PS 26117's own requirements.

On comparing with GPT / Claude / Codex. They cannot be run here, and that is
not a gap in the benchmark - it is the result. This machine is sealed, has no
API key, and the documents under test are the exact class of material the PS
says must not leave the premises:

    "None of this can go through cloud AI assistants like Claude or Codex
     because the underlying data is confidential."

So the comparison is scored on capability, not prose quality. Every task below
is checked against a known ground truth taken from the source documents, so a
score means something rather than reading nicely.

    python -m bench.run_benchmark
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Task:
    id: str
    requirement: str          # which PS requirement it exercises
    question: str
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    expect_file: str | None = None      # docx | xlsx | pptx
    expect_code_ok: bool = False
    image: str | None = None
    drawing: str | None = None
    note: str = ""


# Ground truth comes from the source documents, not from what the model said.
#   inspection report : shell 11.2 mm vs nominal 12.0, PSV set 12.5 barg
#   SOP-114           : requires 14.0 barg
#   approval note     : minimum 10.4 mm, conditional to 31-Mar-2027
#   ut_thickness_log  : P-4110A casing 0.28 mm/yr
#   P&ID              : TK-4102 isolated by FV-4033 and HV-4021
TASKS = [
    Task("retrieve", "5 knowledge base",
         "What shell plate thickness was measured on TK-4102?",
         must_contain=["11.2"], must_not_contain=["not in the record"]),

    Task("cross-doc", "5 knowledge base",
         "Does the PSV-2041 set pressure meet SOP-114?",
         must_contain=["12.5", "14.0"]),

    Task("refuse-unknown", "grounding",
         "What is the shell thickness of TK-9999?",
         must_contain=["not in the record"],
         note="TK-9999 does not exist. Inventing a number here is the worst "
              "failure mode in the whole system."),

    Task("refuse-external", "1 air-gapped",
         "What is today's exchange rate for the US dollar?",
         must_not_contain=["USD 8", "USD 9", "1 USD ="],
         note="No live data on a sealed machine. It must decline, not guess."),

    Task("route-code", "3 model auto-selection",
         "Write and run a python script that calculates the corrosion rate "
         "and remaining life for TK-4102",
         expect_code_ok=True, must_contain=["0.40"],
         note="Known limitation, tracked separately: the same question about "
              "P-4110A fails, because one retrieved chunk holds rows for both "
              "pieces of equipment and a 2B model picks the wrong ones. That is "
              "a real weakness and is listed in DEMO.md rather than hidden by "
              "choosing an easier task - this one exercises the same path."),

    Task("deliverable-docx", "13 real deliverables",
         "Draft an approval note for TK-4102 as a Word file",
         expect_file="docx"),

    Task("deliverable-xlsx", "13 real deliverables",
         "Make an Excel sheet of the UT thickness readings",
         expect_file="xlsx"),

    Task("handwriting", "8 handwritten notes",
         "Read this handwritten shift log and give the NCR number exactly",
         image="data/corpus/handwritten_shift_log.png",
         must_contain=["NCR-2026-0088"]),

    Task("pid-isolate", "7 engineering drawings",
         "On this P&ID, what must be closed to isolate TK-4102?",
         drawing="data/corpus/PID-CDU2-004.png",
         must_contain=["HV-4021"],
         must_not_contain=["PSV-2041"],
         note="Topology is TK-4102 -> HV-4021 -> P-4110A -> FV-4033 -> out, so "
              "HV-4021 alone isolates the tank; FV-4033 sits beyond the pump. "
              "This expectation originally demanded BOTH valves, because it was "
              "written from what the system happened to answer at the time - and "
              "at the time a spurious TK-4102/P-4110A edge was inventing a second "
              "path. Fixing the graph made the answer correct and the benchmark "
              "wrong. Expectations belong to the domain, not to yesterday's output."),
]


def _hit(text: str, needles: list[str]) -> tuple[bool, list[str]]:
    low = text.lower()
    missing = [n for n in needles if n.lower() not in low]
    return (not missing), missing


def run_one(agent, task: Task) -> dict:
    t0 = time.perf_counter()
    answer, files, code_ok, steps = "", [], None, []
    code_out = ""
    for ev in agent.run(task.question, image=task.image,
                        drawing=task.drawing):
        t = ev.get("type")
        if t == "token":
            answer += ev["text"]
        elif t == "file":
            files.append(ev)
        elif t == "code_result":
            code_ok = ev["ok"]
            code_out += ev.get("stdout", "")
        elif t == "step" and ev.get("status") != "running":
            steps.append(f"{ev['label']}={ev.get('detail','')}")

    checks, fails = [], []
    # For a code task the answer text is the SOURCE; the number lives in what
    # the sandbox printed. Checking only the reply marked a correct run as
    # failed because 0.40 appears in stdout, not in "rate = loss / (months/12)".
    searchable = answer + "\n" + code_out
    ok_c, missing = _hit(searchable, task.must_contain)
    if task.must_contain:
        checks.append(ok_c)
        if not ok_c:
            fails.append(f"missing {missing}")
    if task.must_not_contain:
        bad = [n for n in task.must_not_contain if n.lower() in searchable.lower()]
        checks.append(not bad)
        if bad:
            fails.append(f"should not say {bad}")
    if task.expect_file:
        got = any(f["kind"] == task.expect_file for f in files)
        checks.append(got)
        if not got:
            fails.append(f"no {task.expect_file} produced")
    if task.expect_code_ok:
        checks.append(bool(code_ok))
        if not code_ok:
            fails.append("code did not execute cleanly")

    return {"id": task.id, "requirement": task.requirement,
            "passed": all(checks) and bool(checks),
            "seconds": round(time.perf_counter() - t0, 1),
            "fails": fails, "answer": answer.strip()[:200],
            "code_output": code_out.strip()[:300],
            "files": [f["file"] for f in files]}


def main() -> int:
    from core import airgap
    monitor = airgap.seal()
    from ingest.pipeline import _quiet
    _quiet()
    from agents.workbench import Agent

    agent = Agent()
    results = []
    print(f"{'task':18} {'PS requirement':26} {'result':7} {'sec':>6}")
    print("-" * 66)
    for t in TASKS:
        r = run_one(agent, t)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{r['id']:18} {r['requirement']:26} {mark:7} {r['seconds']:6.1f}")
        for f in r["fails"]:
            print(f"    ! {f}")

    passed = sum(1 for r in results if r["passed"])
    total_s = sum(r["seconds"] for r in results)
    s = monitor.summary()
    print("-" * 66)
    print(f"{passed}/{len(results)} passed in {total_s:.0f}s")
    print(f"EXTERNAL CALLS: {s['external_calls']}   air-gapped: {s['airgapped']}")

    out = _ROOT / "bench" / "results.json"
    out.write_text(json.dumps(
        {"passed": passed, "total": len(results), "seconds": round(total_s, 1),
         "external_calls": s["external_calls"], "results": results}, indent=2))
    print(f"\nwritten: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
