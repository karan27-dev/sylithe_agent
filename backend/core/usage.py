"""
Who ran what, on which PC, and what it cost the plant to run it.

THE DEPLOYMENT THIS IS FOR. The plant hosts the models on its own GPU server.
Engineers' PCs run the workbench and call that server over the LAN. Nothing
leaves the site, so there is no vendor bill - and "cost" therefore is not
dollars per token. It is GPU TIME on hardware the plant already bought, plus
the power to run it. A dashboard built around an API invoice answers a
question this deployment never asks.

So the unit of account is engine seconds. Money is derived from them, at a
rate the plant sets in models.yaml from its own capex and tariff, and the
dashboard says which rate it used rather than implying a market price.

WHAT A PLANT IT MANAGER ACTUALLY ASKS
  * is one GPU node enough, and when will it stop being enough
  * which PCs are actually using this, and which are not using it at all
  * who is waiting, and at what time of day
  * which job is eating the node - because a router burning GPU time means a
    large model is doing a small model's work

FLEET WITHOUT A SERVER. Every PC appends to its OWN file. Point
WORKBENCH_USAGE_DIR at a share and the dashboard reads every file in it, so a
fleet view needs a folder rather than a service each PC phones home to - which
would be a network dependency in a product whose claim is that there is none.

Recorded per call, appended and never rewritten:

    ts  machine  user  tier  lane  model  prompt_tokens  output_tokens
    latency_s  fell_back  cost_usd

WHAT IS DELIBERATELY NOT STORED. Not the question, not the answer, not the
documents. A usage log carrying plant content becomes the thing the air gap
exists to prevent, and a token count answers every question asked here.
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

# One file per PC. A shared directory makes the fleet view work with no
# service and no inbound port - each machine only ever appends to its own file,
# so two PCs writing at once cannot corrupt each other.
USAGE_DIR = Path(os.environ.get("WORKBENCH_USAGE_DIR")
                 or (_ROOT / "data" / "usage"))
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
    # Shown instead of the login wherever a person appears. Falls back to the
    # login when it is not known, which is the case for a live instance.
    name: str = ""


def gpu_rate(onprem: dict) -> float:
    """
    Cost of one engine-second on hardware the plant owns.

    Derived, not quoted. A GPU node has a purchase price, a life, and a power
    draw; per-second cost is those three divided out. Stated this way the
    number can be argued with by the person who signs for the hardware, which
    is the only way a cost figure survives a review.
    """
    o = onprem or {}
    capex = float(o.get("node_cost", 0))          # what the node cost
    years = float(o.get("life_years", 4) or 4)    # over how long it is written off
    watts = float(o.get("draw_watts", 0))         # under load
    tariff = float(o.get("power_per_kwh", 0))     # currency per kWh
    hours = years * 365 * 24
    per_hour = (capex / hours if hours else 0) + (watts / 1000.0) * tariff
    return per_hour / 3600.0


def price(rates: dict, model: str) -> tuple[float, float]:
    """(input, output) USD per MILLION tokens. Unknown model prices at 0."""
    r = (rates or {}).get(model)
    if not r:
        return 0.0, 0.0
    return float(r.get("input", 0.0)), float(r.get("output", 0.0))


def record(*, tier: str, lane: str, model: str, prompt_tokens: int,
           output_tokens: int, latency_s: float, fell_back: bool,
           rates: dict | None = None, onprem: dict | None = None) -> None:
    pin, pout = price(rates or {}, model)
    cost = (prompt_tokens / 1e6) * pin + (output_tokens / 1e6) * pout
    # A model with no per-token price is running on the plant's own hardware,
    # so it is charged for the seconds it occupied the node instead. Free at
    # the invoice, not free at the wall.
    if pin == 0 and pout == 0:
        cost = float(latency_s or 0) * gpu_rate(onprem or {})
    call = Call(ts=time.time(), machine=MACHINE, user=USER, tier=tier,
                lane=lane, model=model, prompt_tokens=int(prompt_tokens or 0),
                output_tokens=int(output_tokens or 0),
                latency_s=round(float(latency_s or 0), 3),
                fell_back=bool(fell_back), cost_usd=round(cost, 6),
                name=os.environ.get("WORKBENCH_NAME", ""))
    try:
        with _lock:
            USAGE_DIR.mkdir(parents=True, exist_ok=True)
            with _my_file().open("a") as fh:
                fh.write(json.dumps(asdict(call)) + "\n")
    except Exception:
        # Usage accounting must never break a working answer.
        pass


def _my_file() -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in MACHINE)
    return USAGE_DIR / f"usage-{safe}.jsonl"


def read(since: float | None = None) -> list[dict]:
    """Every machine's file in the usage directory, not just this one."""
    out = []
    if not USAGE_DIR.exists():
        return out
    for f in sorted(USAGE_DIR.glob("usage-*.jsonl")):
        try:
            with f.open() as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if since is None or d.get("ts", 0) >= since:
                        out.append(d)
        except Exception:
            continue
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

    # Capacity, which is the question an owned node actually raises: not
    # "what is the bill" but "is one node enough, and when will it stop being".
    by_hour: dict[int, dict] = {}
    for d in rows:
        _add(by_hour.setdefault(
            int(time.strftime("%H", time.localtime(d.get("ts", 0)))), _empty()), d)

    busiest = max(by_hour.items(), key=lambda kv: kv[1]["seconds"], default=None)
    span_h = max(1e-9, (max((d["ts"] for d in rows), default=0)
                        - min((d["ts"] for d in rows), default=0)) / 3600)
    # Engine-seconds used against wall-clock seconds in the window. Above ~1.0
    # the node is saturated and people are queueing behind each other.
    busy_frac = total["seconds"] / (span_h * 3600) if rows else 0.0

    idle = [m for m in by_machine if by_machine[m]["calls"] == 0]

    tokens = total["prompt_tokens"] + total["output_tokens"]
    return {
        "capacity": {
            "engine_seconds": round(total["seconds"], 1),
            "window_hours": round(span_h, 2),
            "utilisation": round(busy_frac, 4),
            "busiest_hour": busiest[0] if busiest else None,
            "busiest_hour_seconds": round(busiest[1]["seconds"], 1) if busiest else 0,
            "by_hour": [{"hour": h, "seconds": round(v["seconds"], 1),
                         "calls": v["calls"]}
                        for h, v in sorted(by_hour.items())],
            "machines_seen": len(by_machine),
            "machines_idle": idle,
        },
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
