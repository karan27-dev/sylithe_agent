"""
Learning from mistakes, without touching a single weight.

The models here are frozen. Every improvement so far came from a human reading
a failure and changing a prompt - which is not the system learning, it is me
learning. This is the part that lets the system do it.

The literature calls this verbal reinforcement. Reflexion (Shinn et al. 2023)
has the agent write a natural-language post-mortem after a failure and prepends
it next time, reporting 91% vs 80% pass@1 on HumanEval with no fine-tuning.
ExpeL distils such experience into a shared rule library.

We deliberately do NOT use the standard form of it, for one reason. The known
failure mode of reflective memory is self-reinforcing error: an agent that
wrongly concludes something records that conclusion and then never gathers
evidence against it. In a plant, a confidently remembered wrong rule about a
relief valve is worse than no memory at all.

So a lesson is only written when the correct answer is KNOWN - from a
benchmark's ground truth, or from a human correcting an answer. The agent never
grades itself. Every lesson carries where it came from, and can be read and
deleted as a plain JSON file.

Retrieval is by tag and keyword overlap, and lessons enter the prompt as worked
examples rather than rules, because that is what this model family follows -
measured twice on this project: the router went 83% -> 100% on few-shot
examples, and the code lane stopped inventing numbers only after seeing one
worked calculation.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
STORE = _ROOT / "data" / "lessons.json"
_lock = threading.Lock()

MAX_LESSONS = 200
MAX_IN_PROMPT = 3
TAG = re.compile(r"\b([A-Z]{1,4})[-\s]?(\d{2,6})([A-Z])?\b")
STOP = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "of", "to",
        "in", "on", "for", "what", "which", "how", "does", "do", "did", "this",
        "that", "it", "be", "with", "from", "at", "by", "as", "must", "can"}


@dataclass
class Lesson:
    question: str
    was_wrong: str            # what the answer said, briefly
    correct: str              # what it should have said
    why: str = ""             # the rule that makes it correct
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source: str = "benchmark"  # benchmark | user
    ts: float = field(default_factory=time.time)
    hits: int = 0

    def block(self) -> str:
        out = [f"PAST MISTAKE on a question like: {self.question}",
               f"  Wrong answer given: {self.was_wrong}",
               f"  Correct answer:     {self.correct}"]
        if self.why:
            out.append(f"  Because:            {self.why}")
        return "\n".join(out)


def _terms(text: str) -> tuple[list[str], list[str]]:
    up = (text or "").upper()
    tags = []
    for m in TAG.finditer(up):
        t = f"{m.group(1)}-{m.group(2)}{m.group(3) or ''}"
        if t not in tags:
            tags.append(t)
    words = [w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
             if w not in STOP]
    return tags, sorted(set(words))


def load() -> list[Lesson]:
    if not STORE.exists():
        return []
    try:
        raw = json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return []
    out = []
    for d in raw:
        try:
            out.append(Lesson(**{k: v for k, v in d.items()
                                 if k in Lesson.__dataclass_fields__}))
        except Exception:
            continue
    return out


def _write(items: list[Lesson]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(x) for x in items], indent=1))
    tmp.replace(STORE)


def record(question: str, was_wrong: str, correct: str, why: str = "",
           source: str = "benchmark") -> Lesson:
    """
    Write a lesson. Only ever called where the correct answer is known.

    A lesson about the same question is replaced rather than appended, so the
    store does not fill with near-duplicates of one stubborn failure.
    """
    tags, words = _terms(question + " " + correct)
    lesson = Lesson(question=question.strip()[:200],
                    was_wrong=was_wrong.strip()[:200],
                    correct=correct.strip()[:300], why=why.strip()[:200],
                    tags=tags[:6], keywords=words[:14], source=source)
    with _lock:
        items = [l for l in load()
                 if l.question.lower() != lesson.question.lower()]
        items.insert(0, lesson)
        _write(items[:MAX_LESSONS])
    return lesson


def select(question: str, limit: int = MAX_IN_PROMPT) -> list[Lesson]:
    """Past mistakes that look like this question."""
    tags, words = _terms(question)
    tagset, wordset = set(tags), set(words)
    scored = []
    for l in load():
        # A shared equipment tag is much stronger evidence of relevance than a
        # shared common word, so it is weighted accordingly.
        score = 3 * len(tagset & set(l.tags)) + len(wordset & set(l.keywords))
        if score >= 3:
            scored.append((score, l))
    scored.sort(key=lambda x: (-x[0], -x[1].ts))
    return [l for _, l in scored[:limit]]


def block(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    head = ("LESSONS FROM PAST MISTAKES - these were answered wrongly before. "
            "Do not repeat them.")
    return head + "\n\n" + "\n\n".join(l.block() for l in lessons)


def forget(question_substring: str) -> int:
    """Remove lessons. A wrong lesson must be removable, not permanent."""
    with _lock:
        items = load()
        keep = [l for l in items
                if question_substring.lower() not in l.question.lower()]
        _write(keep)
        return len(items) - len(keep)


def stats() -> dict:
    items = load()
    return {"count": len(items),
            "by_source": {s: sum(1 for l in items if l.source == s)
                          for s in {l.source for l in items}},
            "most_used": sorted(((l.hits, l.question) for l in items),
                                reverse=True)[:5]}
