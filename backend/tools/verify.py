"""
Catch a model-written claim that contradicts its own arithmetic or names a
number no retrieved passage actually contains.

Root cause of the failure this fixes: a meeting-prep answer stated "TK-4102
level is 76%, which exceeds the 82% limit" - 76 does not exceed 82. The
model had both correct numbers, copied from the passages correctly, and
still got the comparison backwards. This is not a grounding failure (the
retrieval was right) and not a wrong-number failure (the numbers were
right) - it is a reasoning failure over numbers it already had, which
citation-checking cannot catch and re-retrieval cannot fix.

This does not try to verify prose in general - that is not solvable by
regex. It checks two narrow, mechanical things a small model gets wrong in
exactly the way the corpus of failures found here shows:
  1. A stated A-vs-B relation ("X exceeds Y", "X is below Y", ...) is
     arithmetically checked against the two numbers in the same sentence.
  2. A number written in the answer is checked for appearing verbatim in at
     least one retrieved passage - catching a figure that was not actually
     in what the model was shown.
Neither check understands the domain; both are exact and explainable, which
is what lets a flagged line say WHY it is flagged instead of just looking
suspicious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"(\d+(?:\.\d+)?)\s*(%|mm|barg|degC|yr|mm/yr)?"

# "<num> <relation> <num>", both signs of the relation in one alternation so
# the direction (which side is "exceeds", which is "below") is explicit.
_RELATION = re.compile(
    rf"{_NUM}\s*,?\s*(?:which\s+|currently\s+)?(?:is\s+|does\s+)?"
    r"(exceeds?|is\s+above|is\s+over|is\s+greater\s+than|is\s+more\s+than|"
    r"falls?\s+below|is\s+below|is\s+under|is\s+less\s+than)"
    rf"\s+(?:the\s+)?{_NUM}",
    re.I,
)
_ABOVE_WORDS = {"exceeds", "exceed", "is above", "is over",
                "is greater than", "is more than"}

_NUMBER_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class Issue:
    sentence: str
    reason: str


def _split_sentences(text: str) -> list[str]:
    # Bullets and sentences both matter here, so split on either.
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip(" \t-*") for p in parts if p.strip(" \t-*")]


def _check_relation(sentence: str) -> Issue | None:
    # A citation marker often sits right where the relation pattern expects
    # a comma - "76% [1], which exceeds the 82% limit" - and stops the match
    # cold. Checked against a real answer: this exact sentence, unstripped,
    # matched nothing at all.
    stripped = re.sub(r"\[\d+\]", "", sentence)
    m = _RELATION.search(stripped)
    if not m:
        return None
    a = float(m.group(1))
    relation = m.group(3).lower()
    b = float(m.group(4))
    claims_above = any(w in relation for w in _ABOVE_WORDS)
    actually_above = a > b
    if claims_above != actually_above:
        true_word = "exceeds" if actually_above else "is below"
        return Issue(
            sentence=sentence,
            reason=(f"arithmetic check failed: {a} does not '{relation.strip()}' "
                    f"{b} - {a} {true_word} {b}"),
        )
    return None


def _check_unsupported_numbers(sentence: str, passage_text: str) -> Issue | None:
    # Citation markers ([1], [2], ...) are references, not claimed figures -
    # strip them before scanning, or every cited sentence flags itself on
    # its own citation number.
    without_citations = re.sub(r"\[\d+\]", "", sentence)
    nums = _NUMBER_TOKEN.findall(without_citations)
    if not nums:
        return None
    missing = [n for n in nums if n not in passage_text]
    if missing:
        return Issue(
            sentence=sentence,
            reason=(f"number(s) {', '.join(missing)} do not appear verbatim in "
                    "any retrieved passage"),
        )
    return None


def verify_claims(answer: str, passages: list[str]) -> list[Issue]:
    """
    `passages` are the raw texts of every retrieved passage the answer was
    allowed to cite. A sentence with no citation marker is skipped for the
    unsupported-number check (it may be scaffolding, not a sourced claim);
    the arithmetic check runs on every sentence regardless, since a
    self-contradicting relation is wrong independent of sourcing.
    """
    all_passage_text = "\n".join(passages)
    issues: list[Issue] = []
    for sentence in _split_sentences(answer):
        rel_issue = _check_relation(sentence)
        if rel_issue:
            issues.append(rel_issue)
            continue        # one flag per sentence is enough
        if re.search(r"\[\d+\]", sentence):
            num_issue = _check_unsupported_numbers(sentence, all_passage_text)
            if num_issue:
                issues.append(num_issue)
    return issues


def format_issues(issues: list[Issue]) -> str:
    if not issues:
        return ""
    lines = ["\n\n**⚠️ Needs review** (automatic check found possible issues "
             "with the claims above):"]
    for i in issues:
        lines.append(f'- "{i.sentence}" — {i.reason}')
    return "\n".join(lines)
