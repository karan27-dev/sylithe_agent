"""
Who ran what, on which machine, and what it cost.

The deployment this is written for is one workbench per engineer's PC, all of
them on plant hardware. That answers "is our data safe" and immediately raises
the next question a plant IT manager asks: which machines are actually using
this, how much, and what is the bill.

Recorded per LLM call, appended to data/usage.jsonl and never rewritten:

    ts  machine  user  tier  lane  model  prompt_tokens  output_tokens
    latency_s  fell_back  cost_usd

machine and user come from the OS, not from anything the user types, because
an attribution scheme people can edit is not an attribution scheme. Nothing
leaves the box: this file is read by the dashboard on the same machine.

WHAT IS DELIBERATELY NOT STORED. Not the question, not the answer, not the
documents. A usage log that carries plant content becomes the thing the air
gap exists to prevent, and a token count is enough to answer every question
the dashboard asks.

Local models cost nothing per token, and the log says 0.0 rather than
inventing an electricity figure. That zero is the point of the product, so it
should be visible rather than fudged into looking like a saving.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "usage.jsonl"
_lock = threading.Lock()

# Kept small and boring on purpose: hostname identifies the PC, the OS login
# identifies the person at it. Neither is asked for, so neither can be typed
# wrong or borrowed.
MACHINE = os.environ.get("WORKBENCH_MACHINE") or socket.gethostname()
try:
    USER = os.environ.get("WORKBENCH_USER") or getpass.getuser()
except Exception:                                    # no controlling terminal
    USER = "unknown"


@dataclass
class Call:
    ts: float
    machine: str
    user: str
    tier: str
    lane: str
    model: str
    prompt_tokens: int
    output_tokens: int
    latency_s: float
    fell_back: bool
    cost_usd: float


def price(rates: dict, model: str) -> tuple[float, float]:
    """(input, output) USD per MILLION tokens. Unknown model prices at 0."""
    r = (rates or {}).get(model)
    if not r:
        return 0.0, 0.0
    return float(r.get("input", 0.0)), float(r.get("output", 0.0))


def record(*, tier: str, lane: str, model: str, prompt_tokens: int,
           output_tokens: int, latency_s: float, fell_back: bool,
           rates: dict | None = None) -> None:
    pin, pout = price(rates or {}, model)
    cost = (prompt_tokens / 1e6) * pin + (output_tokens / 1e6) * pout
    call = Call(ts=time.time(), machine=MACHINE, user=USER, tier=tier,
                lane=lane, model=model, prompt_tokens=int(prompt_tokens or 0),
                output_tokens=int(output_tokens or 0),
                latency_s=round(float(latency_s or 0), 3),
                fell_back=bool(fell_back), cost_usd=round(cost, 6))
    try:
        with _lock:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            with STORE.open("a") as fh:
                fh.write(json.dumps(asdict(call)) + "\n")
    except Exception:
        # Usage accounting must never break a working answer.
        pass


def read(since: float | None = None) -> list[dict]:
    if not STORE.exists():
        return []
    out = []
    with STORE.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is None or d.get("ts", 0) >= since:
                out.append(d)
    return out


def _empty() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "seconds": 0.0, "fell_back": 0}


def _add(acc: dict, d: dict) -> None:
    acc["calls"] += 1
    acc["prompt_tokens"] += d.get("prompt_tokens", 0)
    acc["output_tokens"] += d.get("output_tokens", 0)
    acc["cost_usd"] += d.get("cost_usd", 0.0)
    acc["seconds"] += d.get("latency_s", 0.0)
    acc["fell_back"] += 1 if d.get("fell_back") else 0


def summary(days: int = 30) -> dict:
    """Everything the dashboard needs, in one pass over the file."""
    since = time.time() - days * 86400
    rows = read(since)

    total = _empty()
    by_machine: dict[str, dict] = {}
    by_user: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_lane: dict[str, dict] = {}
    by_tier: dict[str, dict] = {}
    by_day: dict[str, dict] = {}

    for d in rows:
        _add(total, d)
        for key, bucket in (
            (d.get("machine", "?"), by_machine),
            (d.get("user", "?"), by_user),
            (d.get("model", "?"), by_model),
            (d.get("lane", "?"), by_lane),
            (d.get("tier", "?"), by_tier),
            (time.strftime("%Y-%m-%d", time.localtime(d.get("ts", 0))), by_day),
        ):
            _add(bucket.setdefault(key, _empty()), d)

    def rank(bucket: dict) -> list[dict]:
        out = [{"key": k, **v} for k, v in bucket.items()]
        out.sort(key=lambda r: -(r["prompt_tokens"] + r["output_tokens"]))
        return out

    tokens = total["prompt_tokens"] + total["output_tokens"]
    return {
        "days": days,
        "since": since,
        "total": {**total, "tokens": tokens,
                  "tokens_per_call": round(tokens / total["calls"], 1)
                  if total["calls"] else 0},
        "machines": rank(by_machine),
        "users": rank(by_user),
        "models": rank(by_model),
        "lanes": rank(by_lane),
        "tiers": rank(by_tier),
        "daily": sorted(({"key": k, **v} for k, v in by_day.items()),
                        key=lambda r: r["key"]),
        "this_machine": MACHINE,
        "this_user": USER,
    }
