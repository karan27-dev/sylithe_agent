"""
Score Client.classify() (the router lane) against a held-out label set,
zero-shot and with the production few-shot examples, back to back.

README and docs/PS26117.md cited a "93% lane accuracy on 15 held-out queries"
number, and separately a "83% to 100% with few-shot examples" number, neither
with a backing script anywhere in the repo - not reproducible, which is worse
than not having the numbers at all. This is that script, for both.

The set below is disjoint from routing.classes[*].examples in
models/models.yaml: different wording, same six classes, so this measures
generalisation rather than re-reading the few-shot prompt back to itself.

    python -m bench.run_router
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# question, expected class name (routing.classes[*].name in models.yaml)
Q: list[tuple[str, str]] = [
    # document - a question about what a specific file/report/note says
    ("what did the last inspection report say about PSV-2041?", "document"),
    ("check if the vessel thickness meets the SOP requirement", "document"),
    ("any open NCRs on E-2404?", "document"),
    ("what's the deviation status for DEV-2026-017?", "document"),

    # pid - engineering drawing / P&ID / line or valve trace
    ("on this drawing, what feeds into TK-4102?", "pid"),
    ("show me the isolation valves upstream of PSV-2041", "pid"),
    ("trace the line from V-2405 to the flare header", "pid"),
    ("which valve on the piping diagram do I shut to isolate the pump?", "pid"),

    # vision - a photo or scan that is not a P&ID
    ("what does this photo show?", "vision"),
    ("can you read the label in this picture?", "vision"),
    ("describe what's in the scanned image", "vision"),
    ("is the text on this nameplate legible?", "vision"),

    # code - a new script/function actually being requested
    ("write me a script to calculate remaining life from thickness readings", "code"),
    ("can you code a function that computes corrosion rate", "code"),
    ("generate python that plots the UT trend over time", "code"),
    ("build a script to convert psi to barg", "code"),

    # reason - general engineering knowledge, comparison, planning
    ("what's the difference between a rupture disc and a PSV", "reason"),
    ("should we replace or repair this exchanger", "reason"),
    ("walk me through the shutdown sequence for a distillation column", "reason"),
    ("why do refineries nitrogen purge before maintenance", "reason"),

    # chitchat - greeting, thanks, "what can you do"
    ("hey there", "chitchat"),
    ("good morning", "chitchat"),
    ("what all can this tool do", "chitchat"),
    ("appreciate the help", "chitchat"),
]


def _run(client, label: str) -> tuple[int, list[dict]]:
    rows = []
    print(f"-- {label} --")
    print(f"{'result':6} {'expected':10} {'got':10}  question")
    print("-" * 78)
    for question, expected in Q:
        t0 = time.perf_counter()
        klass = client.classify(question)
        dt = time.perf_counter() - t0
        ok = klass.name == expected
        rows.append({"question": question, "expected": expected,
                      "got": klass.name, "passed": ok, "seconds": round(dt, 2)})
        print(f"{'PASS' if ok else 'FAIL':6} {expected:10} {klass.name:10}  {question}")
    ok = sum(1 for r in rows if r["passed"])
    print(f"{ok}/{len(rows)} = {100 * ok / len(rows):.0f}%  ({label})")
    print("-" * 78)
    return ok, rows


def main() -> int:
    from core import airgap
    monitor = airgap.seal()
    from core.llm import Client

    client = Client()

    # Zero-shot first, on the SAME client, before restoring the real config -
    # this is what backs the "few-shot took it from X% to Y%" claim in
    # README. Previously that number had no script behind it either.
    saved = [c.examples for c in client.reg.classes]
    for c in client.reg.classes:
        c.examples = []
    zero_ok, zero_rows = _run(client, "zero-shot")
    for c, ex in zip(client.reg.classes, saved):
        c.examples = ex

    few_ok, few_rows = _run(client, "few-shot (production config)")

    s = monitor.summary()
    print(f"zero-shot:  {zero_ok}/{len(Q)} = {100 * zero_ok / len(Q):.0f}%")
    print(f"few-shot:   {few_ok}/{len(Q)} = {100 * few_ok / len(Q):.0f}%")
    print(f"EXTERNAL CALLS: {s['external_calls']}   air-gapped: {s['airgapped']}")
    (_ROOT / "bench" / "router_results.json").write_text(json.dumps({
        "total": len(Q),
        "zero_shot": {"passed": zero_ok,
                       "accuracy_pct": round(100 * zero_ok / len(Q), 1),
                       "results": zero_rows},
        "few_shot": {"passed": few_ok,
                      "accuracy_pct": round(100 * few_ok / len(Q), 1),
                      "results": few_rows},
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
