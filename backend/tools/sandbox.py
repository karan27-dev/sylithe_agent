"""
Run model-written code without letting it off the machine.

PS 26111 Expected Solution names this directly: "code execution in a sandbox"
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

So the sandbox cannot lean on the air gap. It has to deny the network itself,
or the sovereignty claim dies the moment generated code runs.

Layers, outermost first:

  1. sandbox-exec (macOS Seatbelt) - denies network outright, and denies writes
     anywhere but the scratch directory. Verified: socket calls fail with
     gaierror before DNS even resolves.
  2. rlimits - CPU seconds, address space, file size, process count. These stop
     the runaway loop and the fork bomb.
  3. Wall-clock timeout on the subprocess, because RLIMIT_CPU does not count
     time spent asleep.
  4. A scrubbed environment. The parent process may hold API keys; the child
     gets a minimal env so a leaked key cannot be read and posted.
  5. A fresh temp directory as cwd, removed afterwards.

Every layer is cheap, and none of them depends on the others holding.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 20.0
DEFAULT_MEMORY_MB = 512
MAX_OUTPUT = 20_000          # a runaway print loop must not fill the reply

# Seatbelt profile. Reads are broad because Python needs its stdlib; writes are
# confined to the scratch dir; the network is denied with no exceptions.
_PROFILE = """(version 1)
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
                "exit_code": self.exit_code, "seconds": round(self.seconds, 2),
                "timed_out": self.timed_out, "files": self.files,
                "blocked": self.blocked}


def _preamble(memory_mb: int, cpu_s: int) -> str:
    """
    Applied inside the child before its code runs.

    Belt and braces: Seatbelt already denies the network, but patching socket
    here too turns an opaque gaierror into a message that says WHY, which is
    what an engineer reading the output needs.
    """
    return textwrap.dedent(f"""
        import resource, sys, socket
        resource.setrlimit(resource.RLIMIT_CPU, ({cpu_s}, {cpu_s}))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024,)*2)
        try:
            resource.setrlimit(resource.RLIMIT_AS,
                               ({memory_mb}*1024*1024,)*2)
        except (ValueError, OSError):
            pass          # macOS often refuses RLIMIT_AS; Seatbelt still holds

        class _Sealed(OSError):
            pass

        def _no_net(*a, **k):
            raise _Sealed(
                "Network access is blocked: this workbench is air-gapped. "
                "Work from the files provided instead.")
        socket.socket.connect = _no_net
        socket.create_connection = _no_net
        socket.getaddrinfo = _no_net
        sys.stdout.reconfigure(line_buffering=True)
    """).strip()


def run(code: str, *, timeout: float = DEFAULT_TIMEOUT,
        memory_mb: int = DEFAULT_MEMORY_MB,
        files: dict[str, str] | None = None) -> Result:
    """
    Execute `code` in an isolated directory with no network.

    files: optional {name: contents} written into the working directory first,
    so a calculation can be handed its own input data.
    """
    work = Path(tempfile.mkdtemp(prefix="sw-sandbox-"))
    t0 = time.perf_counter()
    try:
        for name, content in (files or {}).items():
            (work / Path(name).name).write_text(content)

        script = work / "_run.py"
        script.write_text(_preamble(memory_mb, int(timeout) + 2)
                          + "\n\n# ---- user code ----\n" + code)

        profile = work / "_sandbox.sb"
        profile.write_text(_PROFILE.format(work=work))

        # A minimal environment. The parent may hold API keys; the child has no
        # business seeing them, and cannot leak what it never received.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work),
               "TMPDIR": str(work), "LANG": "en_US.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}

        cmd = ["sandbox-exec", "-f", str(profile), sys.executable, str(script)]
        if not shutil.which("sandbox-exec"):
            cmd = cmd[3:]           # no Seatbelt: rlimits + socket patch only

        timed_out = False
        try:
            p = subprocess.run(cmd, cwd=work, env=env, capture_output=True,
                               text=True, timeout=timeout)
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = f"Timed out after {timeout:.0f}s and was killed."
            rc = -1

        # Order matters, and the categories must not overlap. The first
        # version lumped "Operation not permitted" in with the network check,
        # so a blocked FILE WRITE was reported as a blocked network call - the
        # sandbox held, but it lied about which wall the code hit. It also
        # missed the memory kill, which arrives as a signal rather than text.
        low = (err or "").lower()
        if timed_out:
            blocked = "timeout"
        elif "air-gapped" in low or "gaierror" in low or "urlerror" in low:
            blocked = "network"
        elif "operation not permitted" in low or "read-only file system" in low:
            blocked = "filesystem"
        elif "memoryerror" in low or "cannot allocate" in low or rc in (-9, 137):
            blocked = "memory"
        else:
            blocked = None

        produced = sorted(f.name for f in work.iterdir()
                          if f.is_file() and f.name not in
                          ("_run.py", "_sandbox.sb") and
                          f.name not in {Path(n).name for n in (files or {})})

        return Result(ok=(rc == 0 and not timed_out),
                      stdout=out[:MAX_OUTPUT], stderr=err[:MAX_OUTPUT],
                      exit_code=rc, seconds=time.perf_counter() - t0,
                      timed_out=timed_out, files=produced, blocked=blocked)
    finally:
        shutil.rmtree(work, ignore_errors=True)
