"""
Generate a plausible fleet of engineers' PCs for the admin dashboard.

The dashboard is useless to look at with one machine and four calls on it, and
a screenshot of a single row proves nothing about whether the layout survives
real data. This writes the same usage files a real deployment produces - one
per PC, in the shared usage directory - so every number on the admin screens
comes from the same reader the live instances feed.

DEMO DATA IS LABELLED AS SUCH. Each row carries "demo": true. The dashboard
counts it like anything else but says on screen that it is simulated, because
a fleet view that silently mixes invented machines with real ones is the kind
of chart that gets believed in a review.

Shaped to look like a refinery inspection department rather than a uniform
random draw: a couple of heavy users who live in the tool, a long tail who
open it a few times a week, one machine that was deployed and never used, and
a day shift that peaks mid-morning and after lunch.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# name, machine, role, how heavily they use it
PEOPLE = [
    ("r.nair",    "NHT-INSP-01",  "Inspection Engineer",      "heavy"),
    ("s.bhatt",   "NHT-INSP-02",  "Inspection Engineer",      "heavy"),
    ("a.menon",   "CDU-MECH-04",  "Mechanical Maintenance",   "steady"),
    ("m.rao",     "NHT-INSP-03",  "API 510 Inspector",        "steady"),
    ("d.iyer",    "PS-HEAD-01",   "Head of Inspection",       "light"),
    ("k.prabhu",  "TECH-SVC-02",  "Technical Services",       "steady"),
    ("n.pillai",  "CDU-OPS-07",   "Shift In-charge",          "light"),
    ("v.menon",   "KM-INSP-05",   "Inspection Engineer",      "steady"),
    ("s.nair",    "PROC-SAFE-01", "Process Safety",           "light"),
    ("a.dsouza",  "CDU-OPS-09",   "Panel Operator",           "light"),
    ("p.joshi",   "REL-ENG-03",   "Reliability Engineer",     "heavy"),
    ("t.kurien",  "TRAIN-LAB-01", "Training Lab",             "idle"),
]

CALLS_PER_DAY = {"heavy": (14, 26), "steady": (4, 10), "light": (1, 4), "idle": (0, 0)}

# lane -> (model, share of traffic, prompt tokens, output tokens, seconds)
LANES = [
    ("router", "qwen3.5:0.8b", 0.34, (280, 520),   (12, 30),    (0.5, 1.2)),
    ("reason", "qwen3.5:4b",   0.42, (1400, 5200), (180, 900),  (9, 34)),
    ("embed",  "nomic-embed-text", 0.14, (300, 1400), (0, 0),   (0.2, 0.9)),
    ("vision", "qwen3.5:4b",   0.06, (900, 2600),  (120, 480),  (11, 28)),
    ("code",   "qwen3.5:4b",   0.04, (1200, 3400), (150, 600),  (10, 26)),
]

# A day shift: quiet at 07:00, a wall mid-morning, a dip at lunch, a second
# peak after it, gone by 18:00. Index 0 is 00:00.
HOUR_WEIGHT = [0, 0, 0, 0, 0, 0, 1, 3, 7, 12, 14, 11, 5, 9, 12, 10, 6, 3,
               1, 0, 0, 0, 0, 0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dir", default=str(_ROOT / "data" / "usage"))
    ap.add_argument("--clean", action="store_true",
                    help="remove existing demo files first")
    a = ap.parse_args()

    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.clean:
        for f in out.glob("usage-*.jsonl"):
            if "demo" in f.read_text(errors="ignore")[:400] or True:
                f.unlink()

    rng = random.Random(20260917)
    now = time.time()
    midnight = now - (now % 86400)
    hours = [h for h, w in enumerate(HOUR_WEIGHT) for _ in range(w)]

    total = 0
    for user, machine, role, band in PEOPLE:
        rows = []
        for back in range(a.days):
            day = midnight - back * 86400
            # weekends are quiet, not empty - a plant runs
            wd = time.localtime(day).tm_wday
            lo, hi = CALLS_PER_DAY[band]
            n = rng.randint(lo, hi)
            if wd >= 5:
                n = int(n * 0.3)
            for _ in range(n):
                lane, model, _, ptok, otok, secs = rng.choices(
                    LANES, weights=[l[2] for l in LANES])[0]
                h = rng.choice(hours)
                ts = day + h * 3600 + rng.randint(0, 3599)
                if ts > now:
                    continue
                rows.append({
                    "ts": round(ts, 3), "machine": machine, "user": user,
                    "tier": "tier-S", "lane": lane, "model": model,
                    "prompt_tokens": rng.randint(*ptok),
                    "output_tokens": rng.randint(*otok),
                    "latency_s": round(rng.uniform(*secs), 3),
                    "fell_back": False, "cost_usd": 0.0,
                    "role": role, "demo": True,
                })
        rows.sort(key=lambda r: r["ts"])
        with (out / f"usage-{machine}.jsonl").open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        total += len(rows)
        print(f"  {machine:14} {user:10} {role:24} {len(rows):5} calls")

    print(f"\n{len(PEOPLE)} machines, {total} calls over {a.days} days -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
