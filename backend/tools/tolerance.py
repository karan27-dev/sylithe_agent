"""
Work out a relief device's acceptance band before the model is asked about it.

Root cause of the failure this fixes: asked whether PSV-2418 could return to
service, the answer was "not acceptable because its as-found set pressure of
24.50 barg exceeds the nameplate requirement of 24.0 barg". Every number was
correct and correctly cited. The tolerance is +/- 3%, the band is 23.28 to
24.72, and 24.50 is inside it - the valve conforms.

Note the direction of the error. A conforming relief device was condemned. In
a plant that means a good valve is pulled off a vessel. An over-strict answer
is not a safe default; it is a different wrong answer, and it costs an
outage.

Everything needed was in the retrieved passages:

    Set Pressure (nameplate), PSV-2418 = 24.0 barg
    Applicable Tolerance, PSV-2418 = API 527 / ASME Sec VIII, +/- 3% of set pressure
    Governing as-found value, As-Found Popping Pressure (barg) = 24.50

and the certificate says in as many words that it has deliberately NOT
evaluated the band, because that is the Inspection Engineer's call. So the
system has to do the multiplication. Asking a 4b model to do it and hoping was
tried: tools/verify.py catches the contradiction afterwards, but only when the
model states a band at all, and here it never mentioned the tolerance.

This is the same split the rest of the project uses. Python computes; the
model explains. A number that is arithmetic is never left to a language model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "Set Pressure (nameplate), PSV-2418 = 24.0 barg"
_SET = re.compile(
    r"set\s+pressure[^=\n]{0,40}?,\s*([A-Z]{2,4}-\d{2,5}[A-Z]?)\s*=\s*"
    r"(\d+(?:\.\d+)?)\s*barg", re.I)

# "Applicable Tolerance, PSV-2418 = ... +/- 3% of set pressure"
_TOL = re.compile(
    r"tolerance[^=\n]{0,40}?,\s*([A-Z]{2,4}-\d{2,5}[A-Z]?)\s*=\s*[^\n]*?"
    r"(?:\+/-|\+\s*/\s*-|±)\s*(\d+(?:\.\d+)?)\s*%", re.I)

# "Governing as-found value, As-Found Popping Pressure (barg) = 24.50"
_FOUND = re.compile(
    r"governing\s+as-?found[^=\n]{0,60}=\s*(\d+(?:\.\d+)?)", re.I)
_LEFT = re.compile(
    r"governing\s+as-?left[^=\n]{0,60}=\s*(\d+(?:\.\d+)?)", re.I)


@dataclass
class Band:
    tag: str
    set_pressure: float
    tolerance_pct: float
    low: float
    high: float
    as_found: float | None = None
    as_left: float | None = None

    @property
    def inside(self) -> bool | None:
        if self.as_found is None:
            return None
        return self.low <= self.as_found <= self.high

    def line(self) -> str:
        head = (f"{self.tag}: set pressure {self.set_pressure:g} barg, "
                f"tolerance +/-{self.tolerance_pct:g}% -> acceptance band "
                f"{self.low:.2f} to {self.high:.2f} barg.")
        if self.as_found is None:
            return head
        verdict = "INSIDE the band (conforming)" if self.inside \
            else "OUTSIDE the band (non-conforming)"
        tail = (f" Governing as-found {self.as_found:g} barg is {verdict}.")
        if self.as_left is not None:
            tail += f" Governing as-left {self.as_left:g} barg."
        return head + tail


def bands_in(passages: list[str]) -> list[Band]:
    """
    Every device whose band can be computed from these passages.

    A device with no stated tolerance produces nothing rather than a guessed
    one - the point is to remove arithmetic from the model, not to invent the
    inputs to it.
    """
    text = "\n".join(passages)
    sets = {m.group(1).upper(): float(m.group(2)) for m in _SET.finditer(text)}
    tols = {m.group(1).upper(): float(m.group(2)) for m in _TOL.finditer(text)}

    out: list[Band] = []
    for tag, sp in sets.items():
        if tag not in tols or sp <= 0:
            continue
        pct = tols[tag]
        b = Band(tag=tag, set_pressure=sp, tolerance_pct=pct,
                 low=sp * (1 - pct / 100), high=sp * (1 + pct / 100))
        # as-found and as-left are reported per certificate, and one set of
        # passages is normally one certificate. With several in play the
        # figures are left off rather than attached to the wrong device.
        found = _FOUND.findall(text)
        left = _LEFT.findall(text)
        if len(sets) == 1:
            if found:
                b.as_found = float(found[0])
            if left:
                b.as_left = float(left[0])
        out.append(b)
    return out


def format_bands(bands: list[Band]) -> str:
    if not bands:
        return ""
    return ("COMPUTED ACCEPTANCE BAND (arithmetic done here, not by you - "
            "quote these figures):\n"
            + "\n".join("  " + b.line() for b in bands))
