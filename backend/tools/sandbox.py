"""
Run model-written code without letting it off the machine.

PS 26117 Expected Solution names this directly: "code execution in a sandbox"
and "A coding task run and verified in a sandbox."

The threat model is model-generated code, not a determined attacker. That code
will not try to break out, but it will cheerfully `pip install`, call an API,
loop forever, or allocate every byte of RAM - and on an 8 GB machine whose swap
is already near full, one runaway allocation freezes the demo.

A measurement that shaped the whole design. core/airgap.py patches sockets
INSIDE the interpreter it runs in. A subprocess is a fresh interpreter, so it
inherits nothing:

    parent: blocked (SovereigntyViolation)
    child : CHILD REACHED THE INTERNET

So the sandbox cannot lean on the air gap alone. What it actually gets depends
on the OS, and that is stated here rather than assumed:

  macOS (sandbox-exec found):
    1. Seatbelt - denies network outright at the OS level, and confines
       writes to the scratch directory. This is what actually stops a
       subprocess the child spawns from reaching the network too; nothing
       Python-level can, since a grandchild process is a fresh OS process.
    2. rlimits - CPU seconds, address space, file size, process count.
    3. core.airgap.seal() too, belt and braces - see _preamble().
    4. Wall-clock timeout, a scrubbed environment, a temp cwd removed after.

  Linux (no Seatbelt, but the `resource` module exists):
    Layers 2-4 above, minus Seatbelt. No OS-level network or filesystem deny -
    a subprocess the sandboxed code spawns is NOT contained. Documented, not
    silently assumed away.

  Windows (no Seatbelt, no `resource` module - both are POSIX-only):
    Only core.airgap.seal() (a socket patch inside the sandboxed
    interpreter), a scrubbed environment, and a wall-clock timeout. THIS IS
    WEAKER: it stops the sandboxed script's own direct network calls, but a
    subprocess that script spawns is not sealed, nor are CPU/memory bounded.
    Do not read "sandboxed" on Windows as "OS-isolated" - it is not. Fixing
    this needs a Windows-native mechanism (a Job Object, an AppContainer);
    none is wired up yet.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TIMEOUT = 20.0
DEFAULT_MEMORY_MB = 512
MAX_OUTPUT = 20_000          # a runaway print loop must not fill the reply
_TRUNCATED = "\n...[truncated]"

# `resource` (rlimits) is POSIX-only - importing it on Windows raises
# ModuleNotFoundError, so this must be a runtime check, not an assumption.
HAS_RESOURCE = sys.platform != "win32"
# sandbox-exec is macOS's Seatbelt CLI. Checked once at import time; if a
# future macOS drops it, this degrades to the rlimit-only layer automatically.
HAS_SEATBELT = sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

# Seatbelt profile: reads are broad because Python needs its stdlib; writes
# are confined to the scratch dir; the network is denied with no exceptions.
_SEATBELT_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
    (subpath "{work}")
    (subpath "/private/var/folders")
    (subpath "/tmp")
    (literal "/dev/null")
    (literal "/dev/stdout")
    (literal "/dev/stderr"))
"""


@dataclass
class Result:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    seconds: float
    timed_out: bool = False
    files: list[str] = field(default_factory=list)
    blocked: str | None = None      # what the sandbox stopped, if anything

    def as_dict(self) -> dict:
        return {"ok": self.ok, "stdout": self.stdout, "stderr": self.stderr,
                "exit_code": self.exit_code, "seconds": round(self.seconds, 3),
                "timed_out": self.timed_out, "files": self.files,
                "blocked": self.blocked}


def _cap(text: str | None) -> str:
    text = text or ""
    return text[:MAX_OUTPUT] + _TRUNCATED if len(text) > MAX_OUTPUT else text


def _preamble(memory_mb: int, cpu_s: int) -> str:
    """
    Applied inside the child before its code runs.

    core.airgap.seal() runs on every platform - it is the ONLY network guard
    on Windows, and on macOS it is belt-and-braces: Seatbelt already denies
    the network, but patching socket too turns an opaque low-level error into
    one that says WHY, which is what an engineer reading the output needs.

    rlimits are added only where the `resource` module exists (HAS_RESOURCE) -
    the unconditional `import resource` in the version this was combined from
    crashed every single run on Windows before the model's code ever executed.
    """
    lines = [
        "import sys",
        f"sys.path.insert(0, {str(_BACKEND_ROOT)!r})",
        "from core import airgap",
        "airgap.seal()",
        "sys.stdout.reconfigure(line_buffering=True)",
    ]
    if HAS_RESOURCE:
        lines += [
            "import resource",
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_s}, {cpu_s}))",
            "resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024,)*2)",
            "try:",
            f"    resource.setrlimit(resource.RLIMIT_AS, ({memory_mb}*1024*1024,)*2)",
            "except (ValueError, OSError):",
            "    pass          # some POSIX systems refuse RLIMIT_AS; other layers still hold",
        ]
    return "\n".join(lines)


def _scrubbed_env(work: Path) -> dict:
    """
    Minimal environment for the child. The parent process may hold API keys
    or other state the sandboxed script has no business seeing - it cannot
    leak what it never received. PATH/SystemRoot are kept on Windows because
    the OS loader needs them to launch python.exe at all.
    """
    if sys.platform == "win32":
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", ""),
        }
    else:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work),
               "TMPDIR": str(work), "LANG": "en_US.UTF-8"}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def _classify_block(stderr: str, exit_code: int, timed_out: bool) -> str | None:
    """
    What wall the code hit, if any - shown in the UI so a failure reads as
    "the sandbox did its job" rather than "the code is broken".

    Order matters, and the categories must not overlap. An earlier version
    lumped "Operation not permitted" in with the network check, so a blocked
    FILE WRITE was reported as a blocked network call - the sandbox held, but
    lied about which wall was hit. It also missed the memory kill, which
    arrives as a signal (-9 / 137) rather than any text at all.
    """
    if timed_out:
        return "timeout"
    low = (stderr or "").lower()
    if ("sovereigntyviolation" in low or "air-gap" in low
            or "gaierror" in low or "urlerror" in low):
        return "network"
    if "operation not permitted" in low or "read-only file system" in low:
        return "filesystem"
    if "memoryerror" in low or "cannot allocate" in low or exit_code in (-9, 137):
        return "memory"
    return None


def run(code: str, *, timeout: float = DEFAULT_TIMEOUT,
        memory_mb: int = DEFAULT_MEMORY_MB,
        files: dict[str, str] | None = None) -> Result:
    """
    Execute `code` in an isolated directory with no network.

    files: optional {name: contents} written into the working directory
    first, so a calculation can be handed its own input data instead of the
    model inventing numbers or trying to reach a file that is not there.

    What "isolated" actually means depends on the OS - see the module
    docstring. This function does not pretend Windows gets what macOS gets.
    """
    work = Path(tempfile.mkdtemp(prefix="sw-sandbox-"))
    t0 = time.perf_counter()
    try:
        for name, content in (files or {}).items():
            (work / Path(name).name).write_text(content, encoding="utf-8")

        script = work / "_run.py"
        script.write_text(
            _preamble(memory_mb, int(timeout) + 2)
            + "\n\n# ---- user code ----\n" + code,
            encoding="utf-8",
        )

        cmd = [sys.executable, "-B", str(script)]
        if HAS_SEATBELT:
            profile = work / "_sandbox.sb"
            profile.write_text(_SEATBELT_PROFILE.format(work=work), encoding="utf-8")
            cmd = ["sandbox-exec", "-f", str(profile)] + cmd

        env = _scrubbed_env(work)

        timed_out = False
        try:
            p = subprocess.run(
                cmd, cwd=work, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            out = exc.stdout or ""
            err = f"Timed out after {timeout:.0f}s and was killed."
            rc = -1

        blocked = _classify_block(err, rc, timed_out)

        produced = sorted(
            f.name for f in work.iterdir()
            if f.is_file() and f.name not in ("_run.py", "_sandbox.sb")
            and f.name not in {Path(n).name for n in (files or {})}
        )

        return Result(
            ok=(rc == 0 and not timed_out),
            stdout=_cap(out), stderr=_cap(err),
            exit_code=rc, seconds=time.perf_counter() - t0,
            timed_out=timed_out, files=produced, blocked=blocked,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


_FENCE_LANG = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.S | re.I)
_FENCE_ANY = re.compile(r"```\w*\s*\n(.*?)```", re.S)
_LOOKS_LIKE_CODE = ("import ", "from ", "def ", "print(", "x =", "#")


def extract_code(text: str) -> str | None:
    """
    The fenced ```python block in a reply, preferred; falls back to any
    fenced block, then to the whole reply if it already looks like code with
    no fence at all (a small model sometimes drops the backticks entirely).
    """
    text = text or ""
    m = _FENCE_LANG.search(text) or _FENCE_ANY.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    t = text.strip()
    if t and any(t.startswith(k) for k in _LOOKS_LIKE_CODE):
        return t
    return None


# A coding model asked for "a function that does X" very often returns
# exactly that - a function definition and nothing that calls it. The script
# then runs, exits 0, and produces no stdout at all: "ran successfully" with
# nothing to show for it. This is the fallback that makes the sandbox
# demonstrate the code rather than just execute it, independent of platform.
DEMO_ARG = 7


def _uncalled_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Top-level functions that no Call node in the module refers to by name."""
    top_level = [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return [f for f in top_level if f.name not in called]


def add_demo_invocation(code: str) -> tuple[str, list[str]]:
    """
    If `code` defines a function that nothing in it ever calls, append a call
    to it so the sandbox proves the code actually does something.

    Only 0- or 1-parameter functions are handled (called with no arguments,
    or with DEMO_ARG) - anything with more parameters, *args/**kwargs, or
    keyword-only arguments is left alone rather than guessing wrong values.

    Returns (code_to_run, demo_calls) - demo_calls is the list of call
    expressions appended, or [] if the code was left unchanged (either it has
    no such function, or it isn't valid Python and run() will report that
    itself).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, []

    demo_calls: list[str] = []
    for f in _uncalled_functions(tree):
        args = f.args
        if args.vararg or args.kwarg or args.kwonlyargs or len(args.args) > 1:
            continue
        demo_calls.append(f"{f.name}({DEMO_ARG})" if args.args else f"{f.name}()")

    if not demo_calls:
        return code, []

    harness = "\n" + "\n".join(f'print({c!r} + " = " + str({c}))' for c in demo_calls) + "\n"
    return code + harness, demo_calls


# ---------------------------------------------------------------------------
# selftest — `python -m tools.sandbox`
# ---------------------------------------------------------------------------

def _selftest() -> int:
    print(f"platform={sys.platform}  HAS_RESOURCE={HAS_RESOURCE}  "
          f"HAS_SEATBELT={HAS_SEATBELT}\n")

    print("1) runs and captures stdout")
    r = run("print('hello from the sandbox')")
    print(f"   ok={r.ok}  stdout={r.stdout!r}")
    assert r.ok and r.stdout.strip() == "hello from the sandbox"

    print("2) wall-clock timeout kills a runaway loop")
    r = run("import time\nwhile True:\n    time.sleep(0.1)\n", timeout=1.5)
    print(f"   timed_out={r.timed_out}  blocked={r.blocked!r}  seconds={r.seconds:.2f}s")
    assert r.timed_out and not r.ok and r.blocked == "timeout"

    print("3) network is blocked and correctly classified")
    r = run(
        "import socket\n"
        "s = socket.socket()\n"
        "s.connect(('example.com', 80))\n"
    )
    tail = r.stderr.strip().splitlines()[-1] if r.stderr else ""
    print(f"   ok={r.ok}  blocked={r.blocked!r}  {tail}")
    assert not r.ok and r.blocked == "network"

    print("4) extract_code pulls the fenced block out of a chatty reply, "
          "and falls back when there is no fence at all")
    reply = "Sure, here you go:\n```python\nprint(1 + 1)\n```\nLet me know if that helps."
    code = extract_code(reply)
    print(f"   fenced: {code!r}")
    assert code == "print(1 + 1)"
    code2 = extract_code("print(1 + 1)")
    print(f"   unfenced: {code2!r}")
    assert code2 == "print(1 + 1)"

    print("5) a defined-but-never-called function is auto-invoked with a sample input")
    code = (
        "def check_odd_even(n):\n"
        "    return 'Odd' if n % 2 else 'Even'\n"
    )
    run_code, demo_calls = add_demo_invocation(code)
    r = run(run_code)
    print(f"   demo_calls={demo_calls}  ok={r.ok}  stdout={r.stdout!r}")
    assert demo_calls == ["check_odd_even(7)"]
    assert r.ok and "Odd" in r.stdout

    print("6) an input file is available to the code, and produced files are reported")
    r = run(
        "with open('in.txt') as f:\n"
        "    n = int(f.read().strip())\n"
        "with open('out.txt', 'w') as f:\n"
        "    f.write(str(n * 2))\n"
        "print('doubled')\n",
        files={"in.txt": "21"},
    )
    print(f"   ok={r.ok}  stdout={r.stdout!r}  files={r.files}")
    assert r.ok and r.stdout.strip() == "doubled" and "out.txt" in r.files

    print("\nall sandbox selftests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
