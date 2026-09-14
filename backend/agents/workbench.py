"""
The agent loop.

 "plans out multi step work, calls local
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
from typing import Iterator

from core.llm import Client, ModelError
from ingest import pipeline
from tools import deliverables as deliv
from tools import sandbox

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

# Queries that point at a document instead of naming a fact. They carry no
# content words, so they embed poorly and score BELOW the relevance floor -
# measured: "what there in this file" 0.473, "summarise this file" 0.491,
# against a 0.50 floor. Left alone they fall through to the no-context prompt
# and the model replies "I cannot read files", which is wrong and alarming.
REFERENTIAL = re.compile(
    r"\b(this|these|the|that|uploaded|attached|above)\s+"
    r"(file|files|document|documents|doc|docs|pdf|report|reports|sheet|scan|"
    r"image|attachment|data|readings|results|findings|numbers|content|info)\b"
    r"|\bwhat'?s? (is |are )?in (it|this|these|the file|the document)\b"
    r"|\b(summari[sz]e|summary of|explain|describe|read) (it|this|these|them)\b",
    re.I)

# Questions about the corpus as a whole - the answer should draw on many
# files, not let one document win every retrieval slot.
BROAD = re.compile(
    r"\b(all|every|each|overall|across)\s+"
    r"(the\s+)?(file|files|document|documents|doc|docs|report|reports)\b"
    r"|\b(everything|whole corpus|all of them|summari[sz]e everything)\b",
    re.I)

# Small models obey examples, not prohibitions. Telling qwen2.5-coder:1.5b
# "do NOT read files" three different ways still produced
# pd.read_excel('ut_thickness_log.xlsx') every time; showing it one worked
# example stopped that immediately. This is the same lesson the router taught:
# few-shot beats rules for this model family.
CODE_SYS = (
    "You write short Python that is EXECUTED immediately in a sandbox with no "
    "network and no files.\n"
    "Copy the numbers you need from the PASSAGES straight into the code as "
    "literals, then compute and print with labels.\n\n"
    "Example of the exact shape required:\n"
    "```python\n"
    "# from the passages\n"
    "nominal_mm  = 12.0\n"
    "measured_mm = 11.2\n"
    "months      = 24\n"
    "min_req_mm  = 10.4\n\n"
    "loss = nominal_mm - measured_mm\n"
    "rate = loss / (months / 12)\n"
    "life = (measured_mm - min_req_mm) / rate\n\n"
    "print(f'loss           {loss:.2f} mm')\n"
    "print(f'corrosion rate {rate:.2f} mm/yr')\n"
    "print(f'remaining life {life:.1f} yr')\n"
    "```\n\n"
    "Return ONE python block in that shape and nothing else."
)

VISION_SYS = (
    "You are reading a photograph or scan of a plant document - often a "
    "handwritten shift log, a nameplate, or a field note.\n"
    "Rules:\n"
    "1. Transcribe what is actually written. Copy every tag and number exactly "
    "as it appears; do not tidy, round or reformat them.\n"
    "2. If a character is genuinely ambiguous (I versus 1, O versus 0, 5 "
    "versus S), say so rather than picking silently - a wrong tag is worse "
    "than a flagged one.\n"
    "3. Never fill in a value you cannot see.\n"
    "4. Answer in English."
)

PID_SYS = (
    "You are a plant engineer reading a P&ID.\n"
    "The DRAWING section gives structure: what equipment exists, its tag, and "
    "what is connected to what. The DOCUMENTS section gives recorded values.\n"
    "Rules:\n"
    "1. A P&ID never states a set pressure, thickness or temperature. If a "
    "number is asked for, take it from the DOCUMENTS section only.\n"
    "2. Never invent a connection that is not listed, and never invent a tag.\n"
    "3. For isolation, list exactly the valves given. A relief valve (PSV/PRV) "
    "is never closed to isolate equipment.\n"
    "4. If something was not detected, say so plainly.\n"
    "5. Short answer, English."
)

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
    "3. NEVER invent an inspection number, tag or finding. NEVER say you "
    "cannot read or access files - you DO read indexed documents; this "
    "particular search simply returned nothing. Ask the user to name the "
    "equipment tag or the document instead.\n"
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
    scope: list[str] = field(default_factory=list)   # restrict to these files
    per_source: int = 0                              # spread hits across files
    no_floor: bool = False                           # ignore the score floor
    pre_tool: str | None = None                      # e.g. analyze_pid


def detect_deliverable(text: str) -> str | None:
    """Does the user want a FILE, or just an answer?"""
    if not ACTION_VERBS.search(text):
        return None
    low = text.lower()
    for pattern, kind in DELIVERABLE_HINTS:
        if re.search(pattern, low, re.I):
            return kind
    return None


# Boilerplate that turns a domain question into a code request. Removed before
# retrieval so the embedding describes the SUBJECT rather than the deliverable.
CODE_FRAMING = re.compile(
    r"\b(write|create|make|generate|give me|produce|build)\b|"
    r"\b(and\s+)?run\b|\bpython\b|\bscript\b|\bprogram\b|\bcode\b|"
    r"\bfunction\b|\ba\s+snippet\b|\bshow the steps\b", re.I)

CODE_BLOCK = re.compile(r"```(?:python|py)?\s*(.*?)```", re.S)


def extract_code(text: str) -> str | None:
    """The fenced block, or the whole reply if it already looks like code."""
    m = CODE_BLOCK.search(text or "")
    if m and m.group(1).strip():
        return m.group(1).strip()
    t = (text or "").strip()
    if t and any(t.startswith(k) for k in
                 ("import ", "from ", "def ", "print(", "x =", "#")):
        return t
    return None


def _how(plan: "Plan", hits: list) -> str:
    """Explain in the activity feed why retrieval behaved the way it did."""
    if plan.scope:
        return f"scoped to {len(plan.scope)} uploaded file(s)"
    if plan.per_source:
        return f"spread across {len({h.chunk.source for h in hits})} files"
    if plan.no_floor:
        return "relevance floor lifted"
    return "above threshold"


class Agent:
    def __init__(self, client: Client | None = None) -> None:
        self.c = client or Client()

    # -- planning ----------------------------------------------------------

    def plan(self, question: str, recent_files: list[str] | None = None) -> Plan:
        kind = detect_deliverable(question)
        recent_files = recent_files or []
        klass = self.c.classify(question)
        lane = klass.lane
        pre_tool = klass.pre_tool
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
        # "what is in this file" -> look only at what was just uploaded, and
        # drop the floor: the floor exists to reject chitchat, not to reject a
        # question that simply has no content words to embed.
        scope, per_source, no_floor = [], 0, False
        # BROAD is checked FIRST: "summarise all the documents" also matches
        # REFERENTIAL (via "the documents"), and scoping that to one uploaded
        # file is exactly the wrong answer.
        if klass.retrieval and BROAD.search(question):
            no_floor, per_source = True, 2
        elif klass.retrieval and REFERENTIAL.search(question):
            no_floor = True
            if recent_files:
                scope = list(recent_files)
            else:
                per_source = 2          # nothing named: sample the corpus

        return Plan(question=question, deliverable=kind,
                    retrieve=klass.retrieval, lane=lane, klass=klass_name,
                    steps=steps, scope=scope, per_source=per_source,
                    no_floor=no_floor, pre_tool=pre_tool)

    # -- execution ---------------------------------------------------------

    def run(self, question: str, history: list[dict] | None = None,
            k: int = 4, recent_files: list[str] | None = None,
            drawing: str | None = None,
            image: str | None = None) -> Iterator[dict]:
        t0 = time.perf_counter()
        history = history or []
        recent_files = recent_files or []

        # 1 -- understand ---------------------------------------------------
        yield {"type": "step", "id": "understand", "status": "running",
               "label": "Understanding request"}
        try:
            plan = self.plan(question, recent_files)
        except (ModelError, Exception) as exc:
            yield {"type": "error", "message": f"planning failed: {exc}"}
            return
        yield {"type": "step", "id": "understand", "status": "done",
               "label": "Understanding request",
               "detail": (f"deliverable: {plan.deliverable}" if plan.deliverable
                          else "answer only")}
        klass_pre_tool = plan.pre_tool
        if klass_pre_tool == "analyze_pid" and drawing:
            plan.steps.insert(2, "Analysing drawing")
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

        # 2b -- pre_tool -----------------------------------------------------
        # models.yaml has declared pre_tool: analyze_pid on the pid class from
        # the start. This is where it finally runs: a drawing is not answerable
        # from the text index, because a P&ID's content is its geometry.
        pid_ctx = ""
        if klass_pre_tool == "analyze_pid" and drawing:
            yield {"type": "step", "id": "pid", "status": "running",
                   "label": "Analysing drawing"}
            try:
                from tools.pid_answer import with_values
                pid = with_values(drawing, client=self.c)
                pid_ctx = pid["text"]
                g = pid["graph"]
                yield {"type": "step", "id": "pid", "status": "done",
                       "label": "Analysing drawing",
                       "detail": (f"{g.number_of_nodes()} items, "
                                  f"{g.number_of_edges()} connections, "
                                  f"{len(pid['tags'])} tags")}
            except Exception as exc:
                yield {"type": "step", "id": "pid", "status": "fail",
                       "label": "Analysing drawing",
                       "detail": f"{type(exc).__name__}: {exc}"}

        # 3 -- retrieve ------------------------------------------------------
        ctx, hits = "", []
        t_r = time.perf_counter()
        if plan.retrieve:
            yield {"type": "step", "id": "retrieve", "status": "running",
                   "label": "Searching corpus"}
            # A code request is mostly boilerplate: "write and run a python
            # script that calculates the corrosion rate for P-4110A casing"
            # embeds as a request for code, not as a question about P-4110A,
            # and the spreadsheet holding 13.7 and 0.28 never surfaces. Strip
            # the framing and retrieve on the subject.
            rq_base = question
            if plan.lane == "code":
                rq_base = CODE_FRAMING.sub(" ", question).strip() or question

            prev = next((m["content"] for m in reversed(history)
                         if m["role"] == "user"), "")
            rq = f"{prev} {rq_base}" if prev and len(question.split()) <= 6 else rq_base
            kk = max(k, 6) if (plan.per_source or plan.scope) else k
            try:
                ctx, hits = pipeline.context(
                    rq, kk, client=self.c,
                    sources=plan.scope or None,
                    per_source=plan.per_source,
                    min_score=0.0 if plan.no_floor else None,
                )
            except Exception as exc:
                yield {"type": "step", "id": "retrieve", "status": "warn",
                       "label": "Searching corpus", "detail": str(exc)}
            # Phrasing should not decide whether an upload is readable.
            # Measured: "...for this data" scores 0.498 and fails the floor,
            # the same sentence with a "?" scores 0.504 and passes. Rather than
            # chase every wording, retry scoped to what the user just uploaded
            # whenever the floor rejected everything. Pattern matching narrows
            # the odds; this closes the gap.
            if not hits and recent_files and not plan.scope:
                plan.scope = list(recent_files)
                plan.no_floor = True
                try:
                    ctx, hits = pipeline.context(
                        rq, max(k, 6), client=self.c,
                        sources=plan.scope, min_score=0.0,
                    )
                except Exception:
                    pass
            dt = time.perf_counter() - t_r
            yield {"type": "step", "id": "retrieve",
                   "status": "done" if hits else "warn",
                   "label": "Searching corpus",
                   "detail": (f"{len(hits)} passages, {_how(plan, hits)} · {dt:.1f}s"
                              if hits else f"no match · {dt:.1f}s")}
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
        # The vision lane is multimodal and has been routed to since the first
        # day, but it never actually received a picture - it was answering
        # image questions from the text index alone. Measured on a handwritten
        # shift log: the OCR path recovers 12 of 14 lines and mangles
        # "Raised NCR-2026-0088" into "R-- 88", while the same model LOOKING at
        # the page gets all 14 including that line. The model was always
        # capable; the image just never arrived.
        looking = plan.lane == "vision" and image
        label = ("Reading the image" if looking else
                 "Extracting findings" if plan.deliverable else "Drafting answer")
        yield {"type": "step", "id": "answer", "status": "running",
               "label": label}

        if plan.lane == "code":
            system = CODE_SYS
            turn = f"PASSAGES:\n{ctx}\n\nTASK: {question}" if ctx else question
        elif looking:
            system = VISION_SYS
            turn = question
        elif pid_ctx:
            system = PID_SYS
            turn = f"{pid_ctx}\n\nQUESTION: {question}"
        else:
            system = GROUNDED_SYS if ctx else NO_CONTEXT_SYS
            turn = f"PASSAGES:\n{ctx}\n\nQUESTION: {question}" if ctx else question
        prompt = history + [{"role": "user", "content": turn}] if history else turn

        answer, reply = "", None
        for ev in self.c.stream(plan.lane, prompt, system=system,
                                images=[image] if looking else ()):
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

        # 4b -- execute --------------------------------------------------------
        # "A coding task run and verified in a sandbox" is named in the PS.
        # Generating code and hoping is not verification; running it is. One
        # retry on failure is also the smallest honest form of the "iterate
        # instead of answering once" requirement - the model sees its own
        # traceback and fixes it.
        if plan.lane == "code":
            code = extract_code(answer)
            if not code:
                yield {"type": "step", "id": "exec", "status": "warn",
                       "label": "Running the code",
                       "detail": "no code block in the reply"}
            else:
                for attempt in (1, 2):
                    yield {"type": "step", "id": "exec", "status": "running",
                           "label": "Running the code"}
                    # The retrieved passages go in as a plain text file too.
                    # The prompt tells the model to inline its numbers, but if
                    # it insists on reading something, let it read the real
                    # context rather than crash on a spreadsheet that is not
                    # there.
                    res = sandbox.run(
                        code, timeout=20,
                        files={"passages.txt": ctx} if ctx else None)
                    yield {"type": "code_result", "attempt": attempt, **res.as_dict()}
                    if res.ok:
                        yield {"type": "step", "id": "exec", "status": "done",
                               "label": "Running the code",
                               "detail": f"exit 0 · {res.seconds:.1f}s"
                                         + (f" · {len(res.files)} file(s)"
                                            if res.files else "")}
                        break
                    detail = res.blocked or f"exit {res.exit_code}"
                    if attempt == 2 or res.blocked in ("network", "timeout"):
                        # A blocked network call or a timeout is not a bug the
                        # model can fix by trying again - it is the sandbox
                        # doing its job. Stop and say so.
                        yield {"type": "step", "id": "exec", "status": "fail",
                               "label": "Running the code", "detail": detail}
                        break
                    yield {"type": "step", "id": "exec", "status": "warn",
                           "label": "Running the code",
                           "detail": f"{detail} - retrying once"}
                    fix = self.c.chat(
                        "code",
                        f"PASSAGES:\n{ctx}\n\n"
                        f"This code failed:\n```python\n{code}\n```\n"
                        f"Error:\n{res.stderr[-600:]}\n\n"
                        "Remember: no files exist in the sandbox. Put the "
                        "numbers from the PASSAGES directly in the code. "
                        "Return the corrected code as ONE python block, "
                        "nothing else.",
                        system=CODE_SYS, max_tokens=700)
                    new_code = extract_code(fix.text)
                    if not new_code:
                        break
                    code = new_code

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
            "grounded": bool(ctx) or bool(pid_ctx),
            "deliverable": plan.deliverable,
            "drawing": drawing,
            "looked_at_image": bool(looking),
            "files": files,
            "total_s": round(time.perf_counter() - t0, 2),
        }
