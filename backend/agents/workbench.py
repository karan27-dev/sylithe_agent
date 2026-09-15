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
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterator

from core.llm import Client, ModelError
from ingest import pipeline
from tools import deliverables as deliv
from tools import sandbox
from tools.brief import context_block
from tools import skills as skill_lib
from tools import lessons as lesson_lib
from tools import actions as actions_tool
from tools import compare_docs as compare_tool
from tools import verify as verify_tool

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
    r"|\b(summari[sz]e|summary of|explain|describe|read) (it|this|these|them)\b"
    # Bare demonstratives with no noun after them. "what is this" carries no
    # noun, so the pattern above missed it entirely - and with several files
    # indexed, retrieval then answered from documents instead of the drawing
    # the user was plainly pointing at.
    r"|^\s*(what|what'?s)\s+(is\s+)?(this|it|that|these)\s*\??\s*$"
    r"|^\s*(explain|describe|summari[sz]e|read)\s+(this|it|that|these)\s*\??\s*$"
    r"|\bwhat (did|have) i (just )?(upload|attach|add)",
    re.I)

# Questions about the corpus as a whole - the answer should draw on many
# files, not let one document win every retrieval slot.
# Questions whose answer is the drawing's geometry, not its prose.
DRAWING_TOPIC = re.compile(
    r"\b(isolat\w*|shut ?off|block in|lock ?out|close|closed|valve|valves|"
    r"manifold|branch|header|connect\w*|downstream|upstream|trace|path|"
    r"line up|equipment on|what is on|p&?id|drawing|diagram)\b", re.I)

BROAD = re.compile(
    r"\b(all|every|each|overall|across)\s+"
    r"(the\s+)?(file|files|document|documents|doc|docs|report|reports)\b"
    r"|\b(everything|whole corpus|all of them|summari[sz]e everything)\b",
    re.I)

# "List pending actions", "action tracker", "what actions are open" - routed
# to a deterministic table extractor (tools/actions.py) rather than ordinary
# retrieval. Measured cause of the bug this avoids: the header chunk of a
# bulletin scores as high as its own action table for a generic query, so
# the table that actually holds the rows never made it into a fixed top-k -
# the model then correctly reported "no actions" about evidence that never
# reached it. Checked before ordinary routing, same as DRAWING_TOPIC below.
ACTION_TRACKER_INTENT = re.compile(
    r"\b(pending\s+actions?|action\s+tracker|open\s+items?)\b"
    r"|\b(list|show|what\s+are)\b.{0,20}\bactions?\b"
    r"|\bactions?\b.{0,30}\b(owner|due\s+date|responsible|deadline)\b"
    r"|\b(owner|due\s+date)s?\b.{0,30}\bactions?\b",
    re.I)

# "compare X with Y", "what changed between REV 1 and REV 2" - routed to a
# deterministic line diff (tools/compare_docs.py). Measured cause of the bug
# this avoids: two documents that share most of their wording embed close
# enough together that retrieval cannot reliably tell them apart, so the
# model ended up mixing an unrelated bulletin's rows into a comparison and
# separately calling an unchanged line "new" because neither copy of it was
# retrieved. A deterministic diff cannot make either mistake.
COMPARE_INTENT = re.compile(
    r"\bcompar(e|ing|ison)\b|\bversus\b|\bvs\.?\b"
    r"|\bwhat\s+(changed|differs?|is\s+different)\b"
    r"|\b(values?|instructions?|findings?)\s+(that\s+)?changed\b"
    r"|\b(old|previous|earlier|original)\s+(and|vs\.?|versus|vs)\s+"
    r"(new|latest|current|revised)\b",
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
    "3. For isolation, copy the valve list EXACTLY as given in the TO ISOLATE "
    "line. Do not add a valve that is not on that line, and do not invent "
    "labels like 'secondary isolation valve' - if one valve is listed, one "
    "valve is the answer. A relief valve (PSV/PRV) is never closed to "
    "isolate equipment.\n"
    "4. If something was not detected, say so plainly.\n"
    "5. When asked what is on the drawing, list EVERY item under EQUIPMENT AND "
    "INSTRUMENTS FOUND - all of them, including every valve. Dropping one from "
    "the list tells the reader it is not there.\n"
    "6. Never mention the section names. The reader sees a drawing, not this "
    "prompt: write 'on the drawing', not 'in the DRAWING section'.\n"
    "7. Short answer, English."
)

ACTIONS_SYS = (
    "You are given a VERIFIED table of action items, extracted directly from "
    "the source documents by code - not by you, and not by search.\n"
    "Rules:\n"
    "1. Present the table's rows clearly. Do not add, remove, merge, "
    "reorder or invent any row, owner or due date beyond what is given.\n"
    "2. If the table is empty, say plainly that no action-item rows were "
    "found for this equipment in the indexed documents - do not guess a "
    "plausible-sounding action instead.\n"
    "3. Keep the Source column or file name attached to each row.\n"
    "4. Answer in English."
)

COMPARE_SYS = (
    "You are given a VERIFIED list of line-level differences between two "
    "documents, computed by a direct text diff - not by you, and not by "
    "search.\n"
    "Rules:\n"
    "1. Summarise ONLY the differences listed. Every change you state must "
    "correspond to a line in the list.\n"
    "2. Do not say a value or instruction changed unless a CHANGED/ADDED/"
    "REMOVED line for it is in the list. If the list is empty, say plainly "
    "that no differences were found - do not invent one to seem useful.\n"
    "3. Do not mention lines that only reformat wording with the same "
    "meaning (e.g. a heading restated) unless the underlying value changed.\n"
    "4. Answer in English."
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
    "6. If a RECENTLY UPLOADED section is present, the user's file IS "
    "available to you - describe it from that section rather than claiming "
    "you cannot see uploaded files.\n"
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

# A filesystem path mentioned in the question itself. "analyse the documents in
# ~/Documents/Plant Manuals" should just work - making someone open a picker to
# repeat a path they already typed is busywork.
#
# Folder names contain spaces ("Sylithe Agent", "Plant Manuals"), so a regex
# that stops at whitespace truncates exactly the paths people actually have.
# Instead: find where a path starts, then extend it a word at a time and keep
# the longest run that is a real directory. The filesystem settles it.
PATH_START = re.compile(r"(~|/Users/|/Volumes/|/home/|\./|/)", re.I)


def find_folder(text: str) -> str | None:
    from pathlib import Path as _P
    text = text or ""
    best = None
    for m in PATH_START.finditer(text):
        words = text[m.start():].split()
        if not words:
            continue
        candidate = ""
        for w in words:
            candidate = f"{candidate} {w}".strip() if candidate else w
            trimmed = candidate.rstrip(".,;:?!)\"'")
            try:
                q = _P(trimmed).expanduser()
            except Exception:
                break
            if q.is_dir() and (best is None or len(str(q)) > len(best)):
                best = str(q)
            # Stop extending once nothing further down this line can exist.
            if not q.exists() and not any(
                    _P(f"{trimmed} {n}").expanduser().exists()
                    for n in words[:3]):
                if best:
                    break
    return best


# Ways of saying "the folder I just picked" without naming it.
MY_FOLDER = re.compile(
    r"\b(my|the|this|that|chosen|selected|current)\s+"
    r"(folder|directory|dir)\b"
    r"|\bfolder\b.*\b(analys|analyz|scan|read|index|summar|how many)"
    r"|\b(how many|count)\b.*\b(files?|documents?|docs?)\b"
    r"|\banalys[ei]\s+(my|the|these|those)\b", re.I)

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


def select_relevant_history(question: str, history: list[dict],
                             keep_recent_turns: int = 1) -> list[dict]:
    """
    Which past exchanges actually belong in THIS prompt.

    Root cause this exists for: chats.history() returns the last few
    messages unconditionally, and every one of them used to go straight into
    the prompt regardless of topic. Checked against a real chat: asking
    about a cylinder-volume script, then an unrelated API653 download
    attempt, then "compare Maintenance Bulletin MB-2026-17 with its REV 2"
    put all three in front of the model for the third question, and the
    same question asked with no history came back different - not because
    of anything the model made up, but because it was handed prior turns
    that had nothing to do with the new one.

    Two rules, and no LLM call to decide between them - a judgment call this
    small a model cannot be trusted to make about its own prompt:
      1. The most recent `keep_recent_turns` exchanges are kept ONLY when
         the question itself signals a continuation - REFERENTIAL text
         ("the above", "this report", "convert it") or a bare follow-up
         with no equipment tag of its own. A question that reads as a fresh
         topic does not get the immediately preceding turn for free just
         because it was immediately preceding: checked against a real chat,
         that rule alone still let an unrelated "download API 653" turn
         leak into the very next, unrelated question.
      2. Anything else, recent or older, is kept only if it shares an
         equipment tag with the current question (TK-4102, PSV-2041, ...),
         via the same tags_in() ingest.pipeline already uses for
         exact-identifier matching. A same-topic follow-up several turns
         later still pulls its earlier context back in.
    """
    if not history:
        return []

    exchanges: list[list[dict]] = []
    i = 0
    while i < len(history):
        pair = [history[i]]
        if (history[i].get("role") == "user" and i + 1 < len(history)
                and history[i + 1].get("role") == "assistant"):
            pair.append(history[i + 1])
            i += 2
        else:
            i += 1
        exchanges.append(pair)

    q_tags = set(pipeline.tags_in(question))
    is_continuation = bool(REFERENTIAL.search(question)) or not q_tags

    recent = (exchanges[-keep_recent_turns:]
              if is_continuation and keep_recent_turns else [])
    older = exchanges[:len(exchanges) - len(recent)] if recent else exchanges

    kept_older = []
    if q_tags:
        for ex in older:
            ex_text = " ".join(m.get("content", "") for m in ex)
            if q_tags & set(pipeline.tags_in(ex_text)):
                kept_older.append(ex)

    result: list[dict] = []
    for ex in kept_older + recent:
        result.extend(ex)
    return result


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

    def plan(self, question: str, recent_files: list[str] | None = None,
             briefs: list | None = None, drawing_present: bool = False) -> Plan:
        kind = detect_deliverable(question)
        recent_files = recent_files or []
        briefs = briefs or []
        klass = self.c.classify(question)
        lane = klass.lane
        pre_tool = klass.pre_tool
        # A deliverable always needs the reasoning lane - the router lane is
        # 32 tokens and cannot emit a JSON document spec.
        if kind and lane == "router":
            lane, klass_name = "reason", "document"
        else:
            klass_name = klass.name

        # Deterministic tools, checked ahead of the router's own class - see
        # ACTION_TRACKER_INTENT / COMPARE_INTENT above for why ordinary
        # retrieval is not trusted with either of these. retrieve is turned
        # off: the tool's own verified output is the only context these two
        # answers should be built from, not whatever ranked retrieval also
        # happens to surface for the same wording.
        retrieve_override: bool | None = None
        if ACTION_TRACKER_INTENT.search(question):
            klass_name, lane, pre_tool = "action_tracker", "reason", "extract_actions"
            retrieve_override = False
        elif COMPARE_INTENT.search(question):
            klass_name, lane, pre_tool = "compare", "reason", "compare_docs"
            retrieve_override = False

        steps = ["Understanding request", "Selecting model"]
        if klass_name == "action_tracker":
            steps.append("Extracting actions")
        elif klass_name == "compare":
            steps.append("Comparing documents")
        elif klass.retrieval:
            steps.append("Searching corpus")
        steps.append("Drafting answer" if not kind else "Extracting findings")
        if kind:
            steps += [f"Building {kind.upper()} spec", f"Writing {kind} file"]
        # What was just uploaded decides how a vague question is answered.
        # "what is this" straight after dropping in a P&ID used to classify as
        # "reason", which ignored the drawing entirely and replied that it
        # could not see any uploaded files. A referential question should
        # follow the newest upload rather than the wording of the sentence.
        # A question about valves, isolation or what is connected can only be
        # answered from the geometry. If a drawing is in play, use it - waiting
        # for the sentence to contain the letters "P&ID" meant that "what must
        # be closed to isolate T-501" answered "there is no information in the
        # text", while the drawing sat unread.
        if drawing_present and DRAWING_TOPIC.search(question):
            klass_name, lane, pre_tool = "pid", "reason", "analyze_pid"

        newest = briefs[0] if briefs else None
        if newest and REFERENTIAL.search(question) and not BROAD.search(question):
            if newest.kind == "drawing":
                klass_name, lane, pre_tool = "pid", "reason", "analyze_pid"
            elif newest.kind in ("scan", "scanned document"):
                klass_name, lane = "vision", "vision"

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

        retrieve = klass.retrieval if retrieve_override is None else retrieve_override
        return Plan(question=question, deliverable=kind,
                    retrieve=retrieve, lane=lane, klass=klass_name,
                    steps=steps, scope=scope, per_source=per_source,
                    no_floor=no_floor, pre_tool=pre_tool)

    # -- execution ---------------------------------------------------------

    def run(self, question: str, history: list[dict] | None = None,
            k: int = 4, recent_files: list[str] | None = None,
            drawing: str | None = None,
            image: str | None = None,
            briefs: list | None = None,
            folder: str | None = None) -> Iterator[dict]:
        t0 = time.perf_counter()
        history_all = history or []
        history = select_relevant_history(question, history_all)
        recent_files = recent_files or []
        briefs = briefs or []
        uploaded = context_block(briefs)

        # 1 -- understand ---------------------------------------------------
        yield {"type": "step", "id": "understand", "status": "running",
               "label": "Understanding request"}
        try:
            plan = self.plan(question, recent_files, briefs,
                             drawing_present=bool(drawing))
        except (ModelError, Exception) as exc:
            yield {"type": "error", "message": f"planning failed: {exc}"}
            return
        bits = []
        if briefs:
            bits.append(f"about {briefs[0].name}")
        bits.append(f"deliverable: {plan.deliverable}" if plan.deliverable
                    else "answer only")
        dropped = len(history_all) - len(history)
        if dropped > 0:
            bits.append(f"{dropped} earlier message(s) set aside as unrelated")
        yield {"type": "step", "id": "understand", "status": "done",
               "label": "Understanding request", "detail": " · ".join(bits)}
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

        # 2a -- a folder named in the question --------------------------------
        # The user can say "analyse everything in ~/Documents/Manuals" instead
        # of opening the picker. Indexing happens before retrieval, so the
        # answer draws on what they just pointed at.
        # A path typed in the question wins; otherwise "my folder", "the
        # folder", "these files" mean the one just chosen in the dialog.
        folder_facts = ""
        folder_path = find_folder(question) or (
            folder if MY_FOLDER.search(question) else None)
        if folder_path:
            yield {"type": "step", "id": "folder", "status": "running",
                   "label": "Reading that folder"}
            try:
                from tools import folder as folder_tool
                scan = folder_tool.ingest(folder_path, client=self.c)
                if scan.error:
                    yield {"type": "step", "id": "folder", "status": "warn",
                           "label": "Reading that folder", "detail": scan.error[:70]}
                else:
                    # "0 files, 0 passages" reads like a failure when it
                    # actually means every file was already indexed and
                    # unchanged - which is the fast path working, not a fault.
                    if scan.indexed:
                        detail = (f"{scan.indexed} new file(s), {scan.chunks} "
                                  f"passages · {scan.seconds:.0f}s")
                    else:
                        detail = (f"{scan.supported} file(s) already indexed - "
                                  "nothing changed")
                    yield {"type": "step", "id": "folder", "status": "done",
                           "label": "Reading that folder", "detail": detail}
                    yield {"type": "folder", **scan.as_dict()}
                    # "How many files are there" is answered from the scan, not
                    # from the documents - the count is a fact about the folder.
                    kinds = ", ".join(f"{v} {k}" for k, v in
                                      sorted(scan.by_type.items(),
                                             key=lambda x: -x[1]))
                    folder_facts = (
                        f"FOLDER JUST READ: {scan.root}\n"
                        f"  {scan.found} files in total, {scan.supported} of them "
                        f"readable by this system.\n"
                        f"  By type: {kinds}\n"
                        f"  Newly indexed this time: {scan.indexed} file(s), "
                        f"{scan.chunks} passages.\n"
                        f"  Files: "
                        + ", ".join(Path(f).name for f in scan.files[:25]))
            except Exception as exc:
                yield {"type": "step", "id": "folder", "status": "fail",
                       "label": "Reading that folder",
                       "detail": f"{type(exc).__name__}: {exc}"}

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

        # 2c -- deterministic table/diff tools --------------------------------
        # extract_actions and compare_docs never touch a model. They are
        # regex/difflib over the real index and the real files on disk, so a
        # row or a "changed" line either exists verifiably or the tool says
        # nothing was found - see tools/actions.py and tools/compare_docs.py
        # for the retrieval bugs this sidesteps.
        actions_ctx = ""
        if klass_pre_tool == "extract_actions":
            yield {"type": "step", "id": "actions", "status": "running",
                   "label": "Extracting actions"}
            tag = next(iter(pipeline.tags_in(question)), None)
            rows = actions_tool.extract_actions(tag)
            table_md = actions_tool.format_table(rows)
            if rows:
                actions_ctx = f"VERIFIED ACTION TABLE (tag={tag or 'any'}):\n{table_md}"
                yield {"type": "step", "id": "actions", "status": "done",
                       "label": "Extracting actions",
                       "detail": f"{len(rows)} row(s)"
                                 + (f" for {tag}" if tag else "")}
            else:
                actions_ctx = (
                    f"No action-item rows were found for tag {tag!r} in the "
                    "indexed documents." if tag else
                    "No action-item rows were found in the indexed documents.")
                yield {"type": "step", "id": "actions", "status": "warn",
                       "label": "Extracting actions", "detail": "0 rows"}

        compare_ctx = ""
        if klass_pre_tool == "compare_docs":
            yield {"type": "step", "id": "compare", "status": "running",
                   "label": "Comparing documents"}
            docs = compare_tool.find_documents(question)
            if len(docs) == 2:
                result = compare_tool.diff_documents(docs[0], docs[1])
                compare_ctx = compare_tool.format_changes(result)
                yield {"type": "step", "id": "compare", "status": "done",
                       "label": "Comparing documents",
                       "detail": (f"{docs[0].source} vs {docs[1].source} · "
                                  f"{len(result.changes)} difference(s)")}
            else:
                found = ", ".join(d.source for d in docs) if docs else "none"
                compare_ctx = (
                    "Could not confidently identify two distinct documents to "
                    f"compare from this question. Candidate(s) found: {found}. "
                    "Ask the user to name both documents more specifically "
                    "(e.g. the exact bulletin number or file name of each).")
                yield {"type": "step", "id": "compare", "status": "warn",
                       "label": "Comparing documents",
                       "detail": f"{len(docs)} candidate(s), need exactly 2"}

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

        # 3b -- skills ---------------------------------------------------------
        # Domain knowledge the passages do not carry: which formula applies,
        # what the standard requires, the mistakes common to this question.
        chosen = skill_lib.select(question)
        if chosen:
            yield {"type": "step", "id": "skills", "status": "done",
                   "label": "Applying skills",
                   "detail": ", ".join(s.name for s in chosen)}
            yield {"type": "skills", "names": [s.name for s in chosen]}
        skill_text = skill_lib.block(chosen)

        # Verified past mistakes on questions like this one. Only written where
        # the right answer was known, never from the model grading itself -
        # the documented failure mode of reflective memory is an agent that
        # confidently remembers a wrong rule and never revisits it.
        past = lesson_lib.select(question)
        if past:
            yield {"type": "step", "id": "lessons", "status": "done",
                   "label": "Recalling past mistakes",
                   "detail": f"{len(past)} similar correction(s)"}
            yield {"type": "lessons",
                   "items": [{"question": l.question, "correct": l.correct}
                             for l in past]}
        lesson_text = lesson_lib.block(past)

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

        # The upload brief belongs on a question that is ABOUT the upload -
        # "what is this", "explain it". Attaching it to every prompt made a
        # specific question drift: "what deviation was found on TK-4102" came
        # back describing the last file uploaded instead of answering. So it
        # goes in when the question is referential, or when there is no
        # retrieved context to answer from, and stays out otherwise.
        refers = bool(REFERENTIAL.search(question)) or plan.scope
        head = f"{uploaded}\n\n" if uploaded and (refers or not ctx) else ""
        if folder_facts:
            head += folder_facts + "\n\n"
        if skill_text:
            head += skill_text + "\n\n"
        if lesson_text:
            head += lesson_text + "\n\n"
        if plan.lane == "code":
            system = CODE_SYS
            turn = f"PASSAGES:\n{ctx}\n\nTASK: {question}" if ctx else question
        elif looking:
            system = VISION_SYS
            turn = head + question
        elif pid_ctx:
            system = PID_SYS
            turn = f"{head}{pid_ctx}\n\nQUESTION: {question}"
        elif actions_ctx:
            system = ACTIONS_SYS
            turn = f"{head}{actions_ctx}\n\nQUESTION: {question}"
        elif compare_ctx:
            system = COMPARE_SYS
            turn = f"{head}{compare_ctx}\n\nQUESTION: {question}"
        else:
            system = GROUNDED_SYS if ctx else NO_CONTEXT_SYS
            turn = (f"{head}PASSAGES:\n{ctx}\n\nQUESTION: {question}"
                    if ctx else head + question)
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

        # What a skill expected and did not find. Surfaced, never silently
        # patched - an answer quietly rewritten to satisfy our own check is
        # harder to trust than one that says what looked wrong.
        for note in skill_lib.verify(answer, chosen):
            yield {"type": "step", "id": "verify", "status": "warn",
                   "label": "Skill check", "detail": note[:80]}

        # A grounded answer can have the right numbers and still get the
        # relationship between them backwards ("76% exceeds 82%") - a
        # reasoning slip citation-checking cannot catch, since both numbers
        # really were in the passages. Checked mechanically, not by asking
        # the same model to grade itself. See tools/verify.py.
        if ctx and hits and plan.lane == "reason":
            issues = verify_tool.verify_claims(answer, [h.chunk.text for h in hits])
            if issues:
                yield {"type": "step", "id": "verify_claims", "status": "warn",
                       "label": "Checking claims",
                       "detail": f"{len(issues)} flagged for review"}
                tail = verify_tool.format_issues(issues)
                answer += tail
                yield {"type": "token", "text": tail}

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
                        # The code is the working; what it PRINTED is the
                        # answer. Leaving the reply as a fenced block meant the
                        # system computed 0.30 mm/yr and then handed back
                        # source code - correct arithmetic, no answer. The
                        # printed lines are appended as the result, because
                        # they are the only numbers here that were executed
                        # rather than written.
                        out = (res.stdout or "").strip()
                        if out:
                            tail = ("\n\n**Result** (run in the sandbox):\n\n"
                                    "```\n" + out[:1200] + "\n```\n")
                            answer += tail
                            yield {"type": "token", "text": tail}
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
