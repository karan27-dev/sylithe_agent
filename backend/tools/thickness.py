"""
Compute a corrosion rate and remaining life before the model is asked for one.

Root cause of the failure this fixes: with the skill corrected, the model
finally reached for the right numbers and the right method - "governing
location CML-04 where thickness dropped from 18.60 mm to 16.60 mm over 4
years" - and then reported 1.08 mm/yr. The answer is 0.50. It also gave a
remaining life of 1.57 years against a true 2.20.

Everything was right except the division. That is the same shape as the
tolerance band: the inputs are in the passages, the arithmetic is one line,
and a 4b model gets it wrong often enough that the answer cannot be trusted.
tools/verify.py catches a contradiction after the fact; this removes the
opportunity.

So Python does it, exactly as tools/tolerance.py does for relief devices, and
the model is handed the result to explain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "CML-04|18.60|16.60" and "CML-04, Survey A (...) = 18.60. CML-04, Survey B
# (...) = 16.60." - docling serialises a table either way depending on source.
_ROW_PIPE = re.compile(
    r"\b(CML-\d{1,3})\b[^\n|]*\|\s*(\d+(?:\.\d+)?)\s*\|\s*(\d+(?:\.\d+)?)")
_ROW_CELL = re.compile(
    r"\b(CML-\d{1,3})\b,\s*Survey\s*A[^=]*=\s*(\d+(?:\.\d+)?)"
    r".{0,120}?\b\1\b,\s*Survey\s*B[^=]*=\s*(\d+(?:\.\d+)?)",
    re.I | re.S)

_INTERVAL = re.compile(
    r"interval[^.\n]{0,60}?(\d+(?:\.\d+)?)\s*(year|yr)", re.I)
_TMIN = re.compile(
    r"(?:minimum required thickness|t-?min)[^=\n]{0,40}[=|]\s*(\d+(?:\.\d+)?)",
    re.I)


@dataclass
class Wastage:
    cml: str
    t_earlier: float
    t_recent: float
    years: float
    t_min: float | None = None

    @property
    def rate(self) -> float:
        return (self.t_earlier - self.t_recent) / self.years

    @property
    def life(self) -> float | None:
        if self.t_min is None or self.rate <= 0:
            return None
        return (self.t_recent - self.t_min) / self.rate

    def line(self) -> str:
        out = (f"governing (thinnest) location {self.cml}: "
               f"{self.t_earlier:g} mm -> {self.t_recent:g} mm over "
               f"{self.years:g} years, so the corrosion rate is "
               f"({self.t_earlier:g} - {self.t_recent:g}) / {self.years:g} = "
               f"{self.rate:.3f} mm/yr.")
        if self.life is not None:
            out += (f" Remaining life is ({self.t_recent:g} - {self.t_min:g})"
                    f" / {self.rate:.3f} = {self.life:.2f} years to t-min "
                    f"{self.t_min:g} mm.")
        return out


def wastage_in(passages: list[str]) -> Wastage | None:
    """
    The governing CML's rate, if two surveys and an interval are present.

    Returns None rather than a guess. One survey cannot give a rate, and
    substituting nominal for the earlier reading is the exact error the
    corrected skill forbids.
    """
    text = "\n".join(passages)
    rows = [(m.group(1), float(m.group(2)), float(m.group(3)))
            for m in _ROW_PIPE.finditer(text)]
    rows += [(m.group(1), float(m.group(2)), float(m.group(3)))
             for m in _ROW_CELL.finditer(text)]
    if not rows:
        return None

    iv = _INTERVAL.search(text)
    if not iv:
        return None
    years = float(iv.group(1))
    if years <= 0:
        return None

    # Governing means thinnest, on the most recent survey.
    cml, t_earlier, t_recent = min(rows, key=lambda r: r[2])
    if t_earlier <= t_recent:
        return None                      # no measurable loss; say nothing

    tm = _TMIN.search(text)
    return Wastage(cml=cml, t_earlier=t_earlier, t_recent=t_recent,
                   years=years, t_min=float(tm.group(1)) if tm else None)


def format_wastage(w: Wastage | None) -> str:
    if w is None:
        return ""
    return ("COMPUTED CORROSION RATE (arithmetic done here, not by you - "
            "quote these figures):\n  " + w.line())
