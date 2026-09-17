"""
Admin dashboard for the whole deployment, not for one PC.

The workbench's own usage panel answers "what have I used". This answers the
questions the person who paid for the GPU node asks: which engineers actually
use it, which machines were deployed and never touched, which model is eating
the node, is one node still enough, and what does the electricity come to.

AUTH IS DELIBERATELY SMALL. One account, from the environment, signed into a
token that lives in memory. It is a lock on a door inside a plant network, not
an identity system - there is no user store, no reset flow and no federation,
and pretending otherwise with a login that looks enterprise-grade would be
worse than saying so. ADMIN_EMAIL / ADMIN_PASSWORD in .env; if they are not
set the dashboard refuses to sign anyone in rather than defaulting to open.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/admin")

SESSIONS: dict[str, float] = {}
SESSION_HOURS = 12

# ---------------------------------------------------------------------------
# per-employee token limits — the only thing this dashboard writes, not just
# reads. A budget an admin sets by hand, kept next to the usage logs rather
# than in models.yaml, because it is deployment policy, not model config.
# ---------------------------------------------------------------------------
_LIMITS_FILE = Path(__file__).resolve().parent.parent / "data" / "admin_limits.json"
_limits_lock = threading.Lock()


def _load_limits() -> dict[str, dict]:
    try:
        return json.loads(_LIMITS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_limits(d: dict[str, dict]) -> None:
    with _limits_lock:
        _LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LIMITS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(_LIMITS_FILE)


def _creds() -> tuple[str, str]:
    return (os.environ.get("ADMIN_EMAIL", "").strip(),
            os.environ.get("ADMIN_PASSWORD", "").strip())


def _ok(token: str | None) -> bool:
    if not token:
        return False
    exp = SESSIONS.get(token)
    if not exp or exp < time.time():
        SESSIONS.pop(token, None)
        return False
    return True


def guard(request: Request):
    tok = (request.headers.get("X-Admin-Token")
           or request.query_params.get("token"))
    return None if _ok(tok) else JSONResponse(
        {"error": "not signed in"}, status_code=401)


@router.post("/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    email, password = _creds()
    if not email or not password:
        return JSONResponse(
            {"ok": False,
             "error": "No admin account configured. Set ADMIN_EMAIL and "
                      "ADMIN_PASSWORD in .env, then restart."},
            status_code=503)
    # compare_digest on both fields: a timing difference on the email would
    # tell an attacker which half they got right.
    good = (hmac.compare_digest(str(body.get("email", "")).strip(), email)
            and hmac.compare_digest(str(body.get("password", "")), password))
    if not good:
        return JSONResponse({"ok": False, "error": "Wrong email or password"},
                            status_code=401)
    tok = secrets.token_urlsafe(24)
    SESSIONS[tok] = time.time() + SESSION_HOURS * 3600
    return JSONResponse({"ok": True, "token": tok, "email": email,
                         "expires_in_h": SESSION_HOURS})


@router.post("/logout")
async def logout(request: Request) -> dict:
    tok = request.headers.get("X-Admin-Token")
    SESSIONS.pop(tok or "", None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# what each model is FOR. The dashboard is read by people who did not choose
# these models, so a name alone is not an answer.
# ---------------------------------------------------------------------------
MODEL_NOTES = {
    "qwen3.5:0.8b": ("Router",
                     "Reads the question and picks which lane answers it. "
                     "Emits 32 tokens of JSON, so it is deliberately the "
                     "smallest model on the box."),
    "qwen3.5:2b":   ("Reasoning (fast)",
                     "Answers document questions when speed matters more than "
                     "a careful verdict."),
    "qwen3.5:4b":   ("Reasoning, code and vision",
                     "Answers grounded questions, writes the short scripts the "
                     "sandbox executes, and reads scans and photographs."),
    "nomic-embed-text": ("Embeddings",
                     "Turns documents and questions into vectors so retrieval "
                     "can find the right passage. Runs on every ingest and "
                     "every search."),
    "deepseek-v4-pro": ("Reasoning (hosted)",
                     "The heavy reasoning tier. Used only when the profile is "
                     "switched to a hosted engine."),
    "deepseek-flash": ("Router / vision (hosted)", "Cheap hosted lane."),
    "gemini-3.5-flash": ("Vision (hosted)",
                     "Reads scanned pages, handwriting and nameplate photos."),
}

LANE_NOTES = {
    "router": "Picks the lane. Cheap by design.",
    "reason": "Answers and judges. This is where a wrong answer costs money.",
    "code":   "Writes a script that is then executed in a sandbox.",
    "vision": "Reads scans, photographs and handwriting.",
    "embed":  "Indexes documents and searches them.",
}


def _catalogue(pricing: dict) -> dict:
    out = {}
    for model, (kind, why) in MODEL_NOTES.items():
        p = pricing.get(model)
        out[model] = {
            "kind": kind, "why": why,
            "hosted": bool(p), "price": p or None,
        }
    return out


# ---------------------------------------------------------------------------
# capacity planning: how far does the server's RAM actually go
# ---------------------------------------------------------------------------
# Weights at 4-bit, plus the KV cache a conversation holds open. Both are the
# numbers that decide how many engineers fit on one node, and both are usually
# guessed at. Sources: model card sizes; KV cache from layers x heads x dim.
MODEL_FOOTPRINT = {
    "qwen3.5:0.8b":     {"weights_gb": 1.0,  "kv_gb_per_8k": 0.18},
    "qwen3.5:2b":       {"weights_gb": 2.7,  "kv_gb_per_8k": 0.42},
    "qwen3.5:4b":       {"weights_gb": 3.4,  "kv_gb_per_8k": 0.60},
    "qwen3.5:9b":       {"weights_gb": 6.6,  "kv_gb_per_8k": 1.10},
    "qwen3.5:27b":      {"weights_gb": 17.0, "kv_gb_per_8k": 2.40},
    "nomic-embed-text": {"weights_gb": 0.27, "kv_gb_per_8k": 0.02},
}
RESERVED_GB = 4.0        # OS, page cache, the server process itself


def _matrix(ram_gb: float, ctx_k: int = 8) -> dict:
    """How many concurrent engineers a node of this size holds, per model."""
    usable = max(0.0, ram_gb - RESERVED_GB)
    rows = []
    for model, f in MODEL_FOOTPRINT.items():
        kv = f["kv_gb_per_8k"] * (ctx_k / 8)
        left = usable - f["weights_gb"]
        seats = int(left // kv) if kv > 0 and left > 0 else 0
        rows.append({
            "model": model,
            "weights_gb": f["weights_gb"],
            "kv_gb": round(kv, 2),
            "fits": left > 0,
            "concurrent": max(0, seats),
        })
    return {"ram_gb": ram_gb, "reserved_gb": RESERVED_GB,
            "usable_gb": round(usable, 1), "context_k": ctx_k, "rows": rows}


# ---------------------------------------------------------------------------
# the data screens
# ---------------------------------------------------------------------------
def _roles() -> dict[str, dict]:
    """machine -> {user, role}, taken from the usage rows themselves."""
    from core import usage
    out: dict[str, dict] = {}
    for d in usage.read():
        m = d.get("machine")
        if m and m not in out:
            out[m] = {"user": d.get("user", "?"), "role": d.get("role", ""),
                      "name": d.get("name", "")}
    return out


@router.post("/limit")
async def set_limit(request: Request) -> dict:
    if (bad := guard(request)):
        return bad
    body = await request.json()
    machine = str(body.get("machine", "")).strip()
    if not machine:
        return JSONResponse({"ok": False, "error": "no machine given"},
                            status_code=400)
    raw = body.get("token_limit")
    limit = int(raw) if raw not in (None, "") else None
    if limit is not None and limit < 0:
        return JSONResponse({"ok": False, "error": "limit cannot be negative"},
                            status_code=400)

    limits = _load_limits()
    if limit is None:
        limits.pop(machine, None)
    else:
        limits[machine] = {"token_limit": limit, "set_at": time.time()}
    _save_limits(limits)
    return {"ok": True, "machine": machine, "token_limit": limit}


@router.get("/overview")
def overview(request: Request, days: int = 30):
    if (bad := guard(request)):
        return bad
    from core import usage
    from core.llm import Client

    c = Client()
    s = usage.summary(days=days)
    rate = usage.gpu_rate(c.reg.onprem)
    secs = s["capacity"]["engine_seconds"]
    roles = _roles()
    limits = _load_limits()

    for row in s["machines"]:
        meta = roles.get(row["key"], {})
        row["user"] = meta.get("user", "")
        row["name"] = meta.get("name", "") or meta.get("user", "")
        row["role"] = meta.get("role", "")
        row["engine_hours"] = round(row["seconds"] / 3600, 2)
        row["energy_kwh"] = round(
            row["seconds"] / 3600 * float(c.reg.onprem.get("draw_watts", 0)) / 1000, 3)
        row["power_cost"] = round(
            row["energy_kwh"] * float(c.reg.onprem.get("power_per_kwh", 0)), 2)
        row["token_limit"] = limits.get(row["key"], {}).get("token_limit")

    watts = float(c.reg.onprem.get("draw_watts", 0))
    kwh = secs / 3600 * watts / 1000
    return {
        **s,
        "onprem": c.reg.onprem,
        "onprem_rate_hour": round(rate * 3600, 4),
        "catalogue": _catalogue(c.reg.pricing),
        "lane_notes": LANE_NOTES,
        "energy": {
            "kwh": round(kwh, 3),
            "tariff": float(c.reg.onprem.get("power_per_kwh", 0)),
            "bill": round(kwh * float(c.reg.onprem.get("power_per_kwh", 0)), 2),
            "per_million_tokens": round(
                kwh / max(1e-9, s["total"]["tokens"] / 1e6)
                * float(c.reg.onprem.get("power_per_kwh", 0)), 2)
            if s["total"]["tokens"] else 0,
        },
    }


@router.get("/person/{machine}")
def person(machine: str, request: Request, days: int = 30):
    if (bad := guard(request)):
        return bad
    import time as _t
    from core import usage
    from core.llm import Client

    c = Client()
    limit = _load_limits().get(machine, {}).get("token_limit")
    rows = [d for d in usage.read(_t.time() - days * 86400)
            if d.get("machine") == machine]
    if not rows:
        return {"machine": machine, "calls": 0, "found": False,
                "token_limit": limit}

    by_lane: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_hour: dict[int, dict] = {}
    tot = usage._empty()
    for d in rows:
        usage._add(tot, d)
        for key, bucket in (
            (d.get("lane", "?"), by_lane),
            (d.get("model", "?"), by_model),
            (_t.strftime("%Y-%m-%d", _t.localtime(d["ts"])), by_day),
            (int(_t.strftime("%H", _t.localtime(d["ts"]))), by_hour),
        ):
            usage._add(bucket.setdefault(key, usage._empty()), d)

    watts = float(c.reg.onprem.get("draw_watts", 0))
    tariff = float(c.reg.onprem.get("power_per_kwh", 0))
    kwh = tot["seconds"] / 3600 * watts / 1000
    listy = lambda b: sorted(({"key": k, **v} for k, v in b.items()),
                             key=lambda r: -(r["prompt_tokens"] + r["output_tokens"]))
    return {
        "found": True, "machine": machine,
        "user": rows[0].get("user", "?"),
        "name": rows[0].get("name", "") or rows[0].get("user", "?"),
        "role": rows[0].get("role", ""),
        "total": {**tot, "tokens": tot["prompt_tokens"] + tot["output_tokens"]},
        "engine_hours": round(tot["seconds"] / 3600, 2),
        "energy_kwh": round(kwh, 3), "power_cost": round(kwh * tariff, 2),
        "lanes": listy(by_lane), "models": listy(by_model),
        "daily": sorted(({"key": k, **v} for k, v in by_day.items()),
                        key=lambda r: r["key"]),
        "hourly": [{"hour": h, "seconds": round(v["seconds"], 1),
                    "calls": v["calls"]} for h, v in sorted(by_hour.items())],
        "first_seen": min(d["ts"] for d in rows),
        "last_seen": max(d["ts"] for d in rows),
        "token_limit": limit,
    }


@router.get("/matrix")
def matrix(request: Request, ram_gb: float = 64, context_k: int = 8):
    if (bad := guard(request)):
        return bad
    return _matrix(ram_gb, context_k)
