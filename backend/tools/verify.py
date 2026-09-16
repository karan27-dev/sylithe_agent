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

# "<value> ... +/- <tol>% ... <reference>" - a tolerance band, which a small
# model states and then judges without ever computing.
#
# Root cause of the failure this fixes: asked whether PSV-7304 conformed, the
# answer was "as-found 20.4 barg exceeds the requirement of +/- 3% around its
# specified 20.0 barg, making it non-conforming". Every number is correct and
# correctly sourced. The band is 19.4 to 20.6, so 20.4 is INSIDE it. The
# model asserted the verdict without doing the multiplication - the same
# shape of error as "76% exceeds 82%", one step further along.
#
# Note which way it is wrong: a conforming relief device was declared
# non-conforming. In a plant that means a good valve is pulled and sent to
# the shop. An over-strict answer is not a safe default; it is just a
# different wrong answer.
_BAND = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:barg|bar|mm|degC)?\b[^.]{0,120}?"
    r"(?:\+/-|\+\s*/\s*-|±|plus or minus)\s*(\d+(?:\.\d+)?)\s*%"
    r"[^.]{0,120}?(\d+(?:\.\d+)?)\s*(?:barg|bar|mm|degC)?\b",
    re.I,
)

# "non-conforming" contains "conforming", so the outside-words are tested
# first and the inside test explicitly excludes a preceding negation.
_OUTSIDE_WORDS = ("non-conform", "nonconform", "not conform", "does not meet",
                  "outside", "exceeds", "fails", "failed", "breach")
_INSIDE_WORDS = ("within", "conforms", "conforming", "acceptable",
                 "satisfies", "meets")


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


def _check_band(sentence: str) -> Issue | None:
    stripped = re.sub(r"\[\d+\]", "", sentence)
    m = _BAND.search(stripped)
    if not m:
        return None
    value, tol, ref = (float(m.group(i)) for i in (1, 2, 3))
    if ref == 0:
        return None
    low, high = ref * (1 - tol / 100), ref * (1 + tol / 100)
    inside = low <= value <= high

    low_s = stripped.lower()
    claims_outside = any(w in low_s for w in _OUTSIDE_WORDS)
    claims_inside = (not claims_outside
                     and any(w in low_s for w in _INSIDE_WORDS))
    if not claims_outside and not claims_inside:
        return None                       # states the band, judges nothing
    if inside == claims_inside:
        return None

    verdict = "inside" if inside else "outside"
    return Issue(
        sentence=sentence,
        reason=(f"band check failed: +/-{tol:g}% of {ref:g} is "
                f"{low:g} to {high:g}, so {value:g} is {verdict} the band"),
    )


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
        band_issue = _check_band(sentence)
        if band_issue:
            issues.append(band_issue)
            continue        # one flag per sentence is enough
        if _BAND.search(re.sub(r"\[\d+\]", "", sentence)):
            # A correctly worked band names numbers no passage contains: the
            # band's own endpoints. Asked to write "+/- 3% of 20.0" out, the
            # answer says "19.4 to 20.6" - derived, checkable, and absent from
            # every source. The unsupported-number check flagged exactly the
            # arithmetic we asked the model to show, so a right answer came
            # back marked "needs review". A sentence whose band checks out has
            # already been verified more strongly than that check can manage.
            continue
        rel_issue = _check_relation(sentence)
        if rel_issue:
            issues.append(rel_issue)
            continue
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
