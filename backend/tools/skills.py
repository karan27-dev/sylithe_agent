"""
Load skills and pick the ones a question needs.

A skill carries what the passages cannot: which formula applies, what the
standard requires, and the mistakes that are common in this kind of question.

Order matters in the injected block - rules first, then a worked example. This
model family follows an example far more reliably than a rule, which was
measured twice on this project: the router went from 83% to 100% on few-shot
examples alone, and the code lane stopped inventing numbers only after being
shown one worked calculation. The rules are there to be read; the example is
there to be copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = _ROOT / "skills"

# More than two skills in one prompt crowds out the passages, and on a 2b model
# the passages are what keep the answer grounded.
MAX_SKILLS = 2


@dataclass
class Skill:
    name: str
    title: str = ""
    when: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    example: str = ""
    checks: list[dict] = field(default_factory=list)

    def score(self, question: str) -> int:
        q = (question or "").lower()
        return sum(1 for w in self.when if w.lower() in q)

    def block(self) -> str:
        out = [f"SKILL - {self.title or self.name}"]
        for r in self.rules:
            out.append("  * " + " ".join(r.split()))
        if self.example:
            out.append("  Worked example:")
            out += ["    " + l for l in self.example.strip().splitlines()]
        return "\n".join(out)


_CACHE: list[Skill] | None = None


# An ISA-style tag or a bare decimal in an example gets copied into answers as
# if it were data - see skills/README.md for the two measured cases. Loading
# refuses rather than warns: a skill that leaks is worse than no skill, because
# the wrong number arrives wearing a citation.
_TAG_IN_EXAMPLE = re.compile(r"\b[A-Z]{2,4}-\d{2,5}[A-Z]?\b")


def _reject_literals(sk: "Skill") -> str | None:
    ex = sk.example or ""
    tag = _TAG_IN_EXAMPLE.search(ex)
    if tag:
        return (f"example names {tag.group(0)!r}; use a <placeholder>, or the "
                f"model will quote it as a fact about other equipment")
    return None


def all_skills(reload: bool = False) -> list[Skill]:
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    out = []
    if SKILLS_DIR.exists():
        for f in sorted(SKILLS_DIR.glob("*.yaml")):
            try:
                d = yaml.safe_load(f.read_text()) or {}
                sk = Skill(
                    name=d.get("name", f.stem), title=d.get("title", ""),
                    when=d.get("when", []) or [], rules=d.get("rules", []) or [],
                    example=d.get("example", "") or "",
                    checks=d.get("checks", []) or [])
                bad = _reject_literals(sk)
                if bad:
                    print(f"  skill {sk.name}: REJECTED - {bad}")
                    continue
                out.append(sk)
            except Exception:
                continue
    _CACHE = out
    return out


def select(question: str, limit: int = MAX_SKILLS) -> list[Skill]:
    scored = [(s.score(question), s) for s in all_skills()]
    hits = sorted((x for x in scored if x[0] > 0), key=lambda x: -x[0])
    return [s for _, s in hits[:limit]]


def block(skills: list[Skill]) -> str:
    return "\n\n".join(s.block() for s in skills) if skills else ""


def verify(answer: str, skills: list[Skill]) -> list[str]:
    """
    Report what a skill expected and did not find.

    Reported, never auto-corrected. A system that silently rewrites an answer
    to satisfy its own check is harder to trust than one that says what looked
    wrong.
    """
    notes = []
    for s in skills:
        for c in s.checks:
            pat = c.get("pattern")
            if not pat:
                continue
            if not re.search(pat, answer or "", re.I):
                notes.append(f"{s.name}: {c.get('why', 'expected ' + pat)}")
    return notes
