"""
The agent loop.

PS 26117 asks for an assistant that "plans out multi step work, calls local
tools ... and iterates on a task instead of answering once and stopping",
and whose output is "real deliverables ... not just chat replies".

So this module owns the plan. `run()` is a generator that yields typed events
as it works, which is what lets the UI show live activity the way a coding
agent does - every step, which model was chosen and why, what came back, and
what was produced.

Event shapes (all dicts with a "type"):
    step    {id, label, status: running|done|warn|fail, detail}
    route   {class, lane, model, why}
    sources {items, retrieval_s}
    token   {text}                     streamed answer text
    thinking{text}
    file    {file, path, kind, bytes}  a deliverable landed on disk
    done    {answer, model, total_s, ...}
    error   {message}

Nothing here talks to the network. The models are local, the tools write to
data/deliverables/, and airgap.seal() is already in force by the time this
runs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from core.llm import Client, ModelError
from ingest import pipeline
from tools import deliverables as deliv

# Words that mean "produce a file", mapped to the writer that should run.
# Checked before the router, because "draft an approval note as a Word file"
# is a document task AND a deliverable task - the router only sees the former.
DELIVERABLE_HINTS: list[tuple[str, str]] = [
    (r"\b(word|docx|\.doc\b|approval note|note|letter|memo|report draft|draft a report)\b", "docx"),
    (r"\b(excel|xlsx|spreadsheet|worksheet|calculation sheet|calc sheet)\b", "xlsx"),
    (r"\b(ppt|pptx|powerpoint|presentation|slides|deck)\b", "pptx"),
]
# A file is only produced when the user actually asks for one to be made.
ACTION_VERBS = re.compile(
    r"\b(draft|make|create|generate|prepare|write|produce|build|export|"
    r"give me a|need a|want a)\b", re.I)

GROUNDED_SYS = (
    "You are a plant inspection assistant. Answer ONLY from the passages "
    "provided. Rules:\n"
    "1. Put a citation like [1] or [2] immediately AFTER each factual claim.\n"
    "2. Never state anything that is not in the passages - say 'not in the "
    "record' instead.\n"
    "3. Reproduce every number, tag and unit exactly as written. Do not round.\n"
    "4. Keep it short - 4 to 6 lines.\n"
    "5. Answer in English."
)

NO_CONTEXT_SYS = (
    "You are the Sovereign Workbench assistant - an on-premise system that "
    "reads plant documents, scans and drawings.\n"
    "Nothing relevant to this question was found in the corpus.\n"
    "Rules:\n"
    "1. For a greeting, reply warmly in one line and say what you can help "
    "with. Do not fire a question back at the user.\n"
    "2. If asked what you can do, say it plainly: grounded answers from "
    "indexed documents, scans (OCR) and reports, with a file and page "
    "citation on every answer, and real Word/Excel/PowerPoint deliverables - "
    "all on this machine with no cloud calls.\n"
    "3. NEVER invent an inspection number, tag or finding.\n"
    "4. This machine is air-gapped - no internet, live data, weather, news or "
    "today's date. Say plainly that you do not have it. Inventing a number is "
    "the worst possible error.\n"
    "5. Do not write citation brackets [1] [2] - there is no source.\n"
    "6. Keep it short - 1 to 3 lines. Answer in English."
)


def _spec_system(kind: str) -> str:
    return (
        "You turn inspection findings into a structured document.\n"
        "Return ONE JSON object and nothing else - no prose, no code fence.\n"
        "Use ONLY facts present in the passages. Copy numbers, tags and units "
        "exactly. Put the passage number in square brackets after each claim, "
        "e.g. 'thickness 11.2 mm [1]'.\n"
        "If a field has no supporting passage, leave it as an empty list or "
        "omit it - never invent content.\n"
        f"Schema to follow:\n{deliv.SCHEMAS[kind]}"
    )


@dataclass
class Plan:
    """What the agent decided to do, before it does it."""
    question: str
    deliverable: str | None = None      # docx | xlsx | pptx | None
    retrieve: bool = True
    lane: str = "reason"
    klass: str = "reason"
    steps: list[str] = field(default_factory=list)


def detect_deliverable(text: str) -> str | None:
    """Does the user want a FILE, or just an answer?"""
    if not ACTION_VERBS.search(text):
        return None
    low = text.lower()
    for pattern, kind in DELIVERABLE_HINTS:
        if re.search(pattern, low, re.I):
            return kind
    return None


class Agent:
    def __init__(self, client: Client | None = None) -> None:
        self.c = client or Client()

    # -- planning ----------------------------------------------------------

    def plan(self, question: str) -> Plan:
        kind = detect_deliverable(question)
        klass = self.c.classify(question)
        lane = klass.lane
        # A deliverable always needs the reasoning lane - the router lane is
        # 32 tokens and cannot emit a JSON document spec.
        if kind and lane == "router":
            lane, klass_name = "reason", "document"
        else:
            klass_name = klass.name

        steps = ["Understanding request", "Selecting model"]
        if klass.retrieval:
            steps.append("Searching corpus")
        steps.append("Drafting answer" if not kind else "Extracting findings")
        if kind:
            steps += [f"Building {kind.upper()} spec", f"Writing {kind} file"]
        return Plan(question=question, deliverable=kind,
                    retrieve=klass.retrieval, lane=lane, klass=klass_name,
                    steps=steps)

    # -- execution ---------------------------------------------------------

    def run(self, question: str, history: list[dict] | None = None,
            k: int = 4) -> Iterator[dict]:
        t0 = time.perf_counter()
        history = history or []

        # 1 -- understand ---------------------------------------------------
        yield {"type": "step", "id": "understand", "status": "running",
               "label": "Understanding request"}
        try:
            plan = self.plan(question)
        except (ModelError, Exception) as exc:
            yield {"type": "error", "message": f"planning failed: {exc}"}
            return
        yield {"type": "step", "id": "understand", "status": "done",
               "label": "Understanding request",
               "detail": (f"deliverable: {plan.deliverable}" if plan.deliverable
                          else "answer only")}
        yield {"type": "plan", "steps": plan.steps,
               "deliverable": plan.deliverable}

        # 2 -- model selection ----------------------------------------------
        model = self.c.profile.lane(plan.lane).model
        why = (f"'{plan.klass}' task -> {plan.lane} lane"
               + (" (deliverable needs a reasoning model)"
                  if plan.deliverable and plan.klass == "document" else ""))
        yield {"type": "step", "id": "route", "status": "done",
               "label": "Selecting model", "detail": f"{model} · {why}"}
        yield {"type": "route", "class": plan.klass, "lane": plan.lane,
               "model": model, "why": why}

        # 3 -- retrieve ------------------------------------------------------
        ctx, hits = "", []
        t_r = time.perf_counter()
        if plan.retrieve:
            yield {"type": "step", "id": "retrieve", "status": "running",
                   "label": "Searching corpus"}
            prev = next((m["content"] for m in reversed(history)
                         if m["role"] == "user"), "")
            rq = f"{prev} {question}" if prev and len(question.split()) <= 6 else question
            try:
                ctx, hits = pipeline.context(rq, k, client=self.c)
            except Exception as exc:
                yield {"type": "step", "id": "retrieve", "status": "warn",
                       "label": "Searching corpus", "detail": str(exc)}
            dt = time.perf_counter() - t_r
            yield {"type": "step", "id": "retrieve",
                   "status": "done" if hits else "warn",
                   "label": "Searching corpus",
                   "detail": (f"{len(hits)} passages above threshold · {dt:.1f}s"
                              if hits else f"no match above threshold · {dt:.1f}s")}
        else:
            yield {"type": "step", "id": "retrieve", "status": "done",
                   "label": "Searching corpus", "detail": "skipped (chitchat)"}

        yield {"type": "sources",
               "retrieval_s": round(time.perf_counter() - t_r, 2),
               "searched": plan.retrieve, "grounded": bool(ctx),
               "items": [
                   {"n": i, "cite": h.chunk.cite(), "source": h.chunk.source,
                    "page": h.chunk.page, "heading": h.chunk.heading,
                    "kind": h.chunk.kind, "score": round(h.score, 3),
                    "text": h.chunk.text}
                   for i, h in enumerate(hits, 1)],
               }

        # 4 -- answer --------------------------------------------------------
        label = "Extracting findings" if plan.deliverable else "Drafting answer"
        yield {"type": "step", "id": "answer", "status": "running",
               "label": label}

        system = GROUNDED_SYS if ctx else NO_CONTEXT_SYS
        turn = f"PASSAGES:\n{ctx}\n\nQUESTION: {question}" if ctx else question
        prompt = history + [{"role": "user", "content": turn}] if history else turn

        answer, reply = "", None
        for ev in self.c.stream(plan.lane, prompt, system=system):
            if ev["type"] == "token":
                answer += ev["text"]
                yield {"type": "token", "text": ev["text"]}
            elif ev["type"] == "thinking":
                yield {"type": "thinking", "text": ev["text"]}
            elif ev["type"] == "done":
                reply = ev["reply"]

        yield {"type": "step", "id": "answer", "status": "done", "label": label,
               "detail": (f"{reply.output_tokens} tokens · "
                          f"{reply.tok_per_s:.1f} tok/s" if reply else "")}

        # 5 -- deliverable ----------------------------------------------------
        files: list[dict] = []
        if plan.deliverable:
            kind = plan.deliverable
            yield {"type": "step", "id": "spec", "status": "running",
                   "label": f"Building {kind.upper()} spec"}
            # The findings are already established, so the spec call gets the
            # short version of them rather than the passages twice over -
            # a long prompt was what truncated the JSON on the first attempt.
            spec_reply = self.c.chat(
                plan.lane,
                f"PASSAGES:\n{ctx}\n\nTASK: {question}\n\n"
                "Return the JSON object now, complete, starting with {.",
                system=_spec_system(kind),
                max_tokens=2200,
            )
            spec = deliv.parse_spec(spec_reply.text, kind)
            if not spec:
                yield {"type": "step", "id": "spec", "status": "fail",
                       "label": f"Building {kind.upper()} spec",
                       "detail": f"no usable JSON ({spec_reply.output_tokens} tokens returned)"}
                yield {"type": "step", "id": "write", "status": "fail",
                       "label": f"Writing {kind} file",
                       "detail": "skipped - no spec"}
            else:
                fields = ", ".join(list(spec)[:5])
                yield {"type": "step", "id": "spec", "status": "done",
                       "label": f"Building {kind.upper()} spec",
                       "detail": f"fields: {fields}"}
                yield {"type": "step", "id": "write", "status": "running",
                       "label": f"Writing {kind} file"}
                try:
                    src = [{"n": i, "cite": h.chunk.cite()}
                           for i, h in enumerate(hits, 1)]
                    d = deliv.build(kind, spec, src)
                    files.append(d.as_dict())
                    yield {"type": "step", "id": "write", "status": "done",
                           "label": f"Writing {kind} file",
                           "detail": f"{d.path.name} · {d.bytes/1024:.1f} KB"}
                    yield {"type": "file", **d.as_dict()}
                except Exception as exc:
                    yield {"type": "step", "id": "write", "status": "fail",
                           "label": f"Writing {kind} file",
                           "detail": f"{type(exc).__name__}: {exc}"}

        yield {
            "type": "done",
            "answer": answer,
            "model": reply.model if reply else model,
            "lane": plan.lane,
            "profile": reply.profile if reply else self.c.profile_name,
            "latency_s": round(reply.latency_s, 2) if reply else 0,
            "tok_per_s": round(reply.tok_per_s, 1) if reply else 0,
            "output_tokens": reply.output_tokens if reply else 0,
            "fell_back": reply.fell_back if reply else False,
            "retried": reply.retried_for_truncation if reply else 0,
            "grounded": bool(ctx),
            "deliverable": plan.deliverable,
            "files": files,
            "total_s": round(time.perf_counter() - t0, 2),
        }
