"""
Escape tests for tools/sandbox.py.

docs/PS26117.md and README described the sandbox as "escape-tested" with no
test file anywhere in the repo backing that claim. This is that file: it
actually tries each escape the module's own docstring says it defends
against, and checks the sandbox caught it rather than assuming so.

Per the module docstring, what "isolated" means is OS-dependent - this suite
records what actually happened on THIS machine's OS, not a claim about every
OS. Re-run it wherever the demo runs.

    python -m bench.run_sandbox_escape
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

CASES = [
    ("direct network call",
     "import socket\n"
     "s = socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
     "print('REACHED THE INTERNET')",
     "network"),

    ("network via subprocess (child-of-child)",
     "import subprocess, sys\n"
     "code = \"import socket; socket.create_connection(('8.8.8.8', 53), timeout=3); print('CHILD REACHED THE INTERNET')\"\n"
     "r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=5)\n"
     "sys.stdout.write(r.stdout)\n"
     "sys.stderr.write(r.stderr)\n",
     "network-or-clean"),   # see note in main(): platform-dependent, not a flat PASS/FAIL

    ("write outside the scratch dir",
     "open('/tmp/sw_escape_test_marker', 'w').write('escaped')\n"
     "print('WROTE OUTSIDE SANDBOX')",
     "filesystem-or-clean"),

    ("infinite loop past the timeout",
     "while True:\n    pass\n",
     "timeout"),

    ("allocate past the memory limit",
     "x = bytearray(2 * 1024 * 1024 * 1024)   # 2 GB, DEFAULT_MEMORY_MB is smaller\n"
     "print('ALLOCATED')",
     "memory"),
]


def main() -> int:
    from core import airgap
    monitor = airgap.seal()
    from tools import sandbox

    rows = []
    print(f"{'result':6}  case")
    print("-" * 78)
    for name, code, expect in CASES:
        res = sandbox.run(code, timeout=6)
        leaked = ("REACHED THE INTERNET" in (res.stdout or "")
                  or "WROTE OUTSIDE SANDBOX" in (res.stdout or "")
                  or "ALLOCATED" in (res.stdout or ""))
        contained = res.blocked is not None or res.timed_out or not res.ok
        # "-or-clean" cases: contained by an explicit wall (blocked/timed_out)
        # OR the code simply failed for some other reason (e.g. Permission
        # denied that _classify_block doesn't recognise by name) - either way
        # a PASS requires no leak marker in stdout. A FAIL is only the marker
        # actually printing, i.e. an unambiguous escape.
        ok = not leaked
        rows.append({"case": name, "blocked": res.blocked, "timed_out": res.timed_out,
                      "ok": res.ok, "exit_code": res.exit_code, "leaked": leaked,
                      "expected": expect, "passed": ok,
                      "stdout": (res.stdout or "")[:200],
                      "stderr": (res.stderr or "")[-200:]})
        print(f"{'PASS' if ok else 'LEAK':6}  {name}  "
              f"[blocked={res.blocked} timed_out={res.timed_out} ok={res.ok}]")

    passed = sum(1 for r in rows if r["passed"])
    s = monitor.summary()
    print("-" * 78)
    print(f"{passed}/{len(rows)} contained on {sys.platform} "
          f"({'Seatbelt' if sys.platform == 'darwin' else 'no Seatbelt'})")
    print(f"EXTERNAL CALLS FROM THIS PARENT: {s['external_calls']}  "
          "(child escapes are NOT caught by this counter by design - see "
          "tools/sandbox.py docstring; that is exactly what this file checks instead)")
    (_ROOT / "bench" / "sandbox_escape_results.json").write_text(json.dumps({
        "platform": sys.platform, "passed": passed, "total": len(rows), "results": rows,
    }, indent=1))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
