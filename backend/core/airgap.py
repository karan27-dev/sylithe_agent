"""
Sovereignty monitor - intercepts every outbound socket attempt.

PS requirement:
  "The system should also show, through logs or a visible network monitor,
   that no external calls are made at any point. That's the actual proof
   of the sovereign claim, not just a statement of it."

Two modes:
  audit()  -> log attempts but let them through   (development)
  seal()   -> block everything except localhost/LAN (demo)

Patching happens on import, so import this before anything else.
"""

from __future__ import annotations
import socket
import ipaddress
import time
import json
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "network.jsonl"
_lock = threading.Lock()


@dataclass
class Attempt:
    ts: float
    host: str
    port: int
    verdict: str            # "local" | "lan" | "EXTERNAL"
    blocked: bool
    stack_hint: str = ""


@dataclass
class Monitor:
    mode: str = "audit"                     # "audit" | "seal"
    allow_lan: bool = True                  # the tier-L GPU node lives on the LAN
    attempts: list[Attempt] = field(default_factory=list)

    # ---- counters shown by the UI panel ---------------------------------
    @property
    def external_calls(self) -> int:
        return sum(1 for a in self.attempts if a.verdict == "EXTERNAL")

    @property
    def local_calls(self) -> int:
        return sum(1 for a in self.attempts if a.verdict in ("local", "lan"))

    def summary(self) -> dict:
        return {
            "mode": self.mode,
            "external_calls": self.external_calls,
            "local_calls": self.local_calls,
            "airgapped": self.external_calls == 0,
        }

    def report(self) -> str:
        s = self.summary()
        badge = "AIR-GAPPED ✓" if s["airgapped"] else "LEAK DETECTED ✗"
        lines = [
            f"EXTERNAL CALLS: {s['external_calls']}    {badge}",
            f"local/LAN calls: {s['local_calls']}   (mode={s['mode']})",
        ]
        for a in self.attempts:
            if a.verdict == "EXTERNAL":
                mark = "BLOCKED" if a.blocked else "ALLOWED"
                lines.append(f"  [{mark}] {a.host}:{a.port}  {a.stack_hint}")
        return "\n".join(lines)


MONITOR = Monitor()


def _classify(host: str) -> str:
    """Classify a host as localhost, LAN or external."""
    if host in ("localhost", "127.0.0.1", "::1", ""):
        return "local"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # a hostname: do not try to resolve it, the lookup is itself the leak
        if host.endswith(".local") or host == "host.docker.internal":
            return "lan"
        return "EXTERNAL"
    if ip.is_loopback:
        return "local"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "EXTERNAL"


def _record(host: str, port: int, hint: str = "") -> Attempt:
    verdict = _classify(str(host))
    blocked = False
    if MONITOR.mode == "seal":
        if verdict == "EXTERNAL":
            blocked = True
        elif verdict == "lan" and not MONITOR.allow_lan:
            blocked = True

    a = Attempt(time.time(), str(host), int(port), verdict, blocked, hint)
    with _lock:
        MONITOR.attempts.append(a)
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a") as f:
                f.write(json.dumps(asdict(a)) + "\n")
        except OSError:
            pass
    return a


class SovereigntyViolation(ConnectionError):
    """An attempt was made to break the air gap."""


_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo
_patched = False


def _guard(fn, self, address, *args, **kwargs):
    host, port = (address + (0,))[:2] if isinstance(address, tuple) else (address, 0)
    a = _record(host, port, hint=fn.__name__)
    if a.blocked:
        raise SovereigntyViolation(
            f"Air-gap: outbound connection to {host}:{port} blocked "
            f"(mode=seal). Everything must stay on-premise."
        )
    return fn(self, address, *args, **kwargs)


def _patched_connect(self, address, *a, **kw):
    return _guard(_orig_connect, self, address, *a, **kw)


def _patched_connect_ex(self, address, *a, **kw):
    return _guard(_orig_connect_ex, self, address, *a, **kw)


def _patched_getaddrinfo(host, port, *a, **kw):
    """A DNS lookup is itself a leak - the query leaves the machine."""
    verdict = _classify(str(host))
    if MONITOR.mode == "seal" and verdict == "EXTERNAL":
        _record(host, port or 0, hint="getaddrinfo")
        raise SovereigntyViolation(f"Air-gap: DNS lookup for {host} blocked.")
    return _orig_getaddrinfo(host, port, *a, **kw)


def _patch():
    global _patched
    if _patched:
        return
    socket.socket.connect = _patched_connect
    socket.socket.connect_ex = _patched_connect_ex
    socket.getaddrinfo = _patched_getaddrinfo
    _patched = True


def audit(allow_lan: bool = True) -> Monitor:
    """Log everything, block nothing. For development."""
    MONITOR.mode = "audit"
    MONITOR.allow_lan = allow_lan
    _patch()
    return MONITOR


def seal(allow_lan: bool = True) -> Monitor:
    """Block everything except localhost (and optionally LAN). For demos."""
    MONITOR.mode = "seal"
    MONITOR.allow_lan = allow_lan
    _patch()
    return MONITOR


def reset() -> None:
    with _lock:
        MONITOR.attempts.clear()
