"""
Model router - the client that drives models.yaml.

Requirement:
  "New open weight models should be addable later without redesigning
   the system."

So no model name is hard-coded in this file. Everything comes from
models/models.yaml. A new model is 6 lines of YAML.

Two backends:
  ollama   -> /api/chat, supports think + keep_alive + hot-swap   (tier-S)
  openai   -> /v1/chat/completions, vLLM/TGI compatible           (tier-L)

Usage:
    from core import airgap; airgap.seal()
    from core.llm import Client

    c = Client()                        # active_profile from the YAML
    c.chat("reason", "What is the status of TK-4102?")
    c.classify("this is a P&ID drawing")   # -> "pid"
    c.embed(["passage one", "passage two"])
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import httpx
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_YAML_PATH = _ROOT / "models" / "models.yaml"

# ---------------------------------------------------------------------------
# config objects
# ---------------------------------------------------------------------------


class ModelError(RuntimeError):
    """A lane call failed and no fallback remained."""


@dataclass
class Lane:
    """One line of work: router / reason / code / vision / embed."""

    name: str
    model: str
    think: bool = False
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_s: float = 120.0
    dim: int | None = None            # embed lane only
    # DeepSeek V4 Pro exposes thinking depth as reasoning_effort ("high" is
    # its default, "max" is the ceiling) rather than through the chat template.
    # Left unset the parameter is not sent at all, so an endpoint that has
    # never heard of it is unaffected.
    reasoning_effort: str | None = None

    # A lane may pin its own engine instead of using the profile's.
    #
    # Needed because the embed lane is not like the others. models.yaml said
    # "embed: nomic-embed-text" under tier-L and read as local, but only the
    # MODEL NAME is per-lane - the endpoint came from the profile, so the call
    # went to https://api.deepseek.com/v1/embeddings and came back 401. The
    # comment claiming embeddings stayed local was simply wrong.
    #
    # It matters beyond the error: switching tiers must not silently change
    # the embedding model, because that invalidates every vector in the index.
    #
    # It also lets one profile mix vendors, which is the point of lanes: the
    # strongest reasoner and the strongest vision model are not the same
    # company's, and a lane is the right unit at which to say so.
    endpoint: str | None = None
    api_key_env: str | None = None

    @property
    def api_key(self) -> str | None:
        import os
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @classmethod
    def from_yaml(cls, name: str, raw: dict, defaults: dict) -> "Lane":
        merged = {**defaults, **(raw or {})}
        return cls(
            name=name,
            model=merged["model"],
            think=bool(merged.get("think", False)),
            temperature=float(merged.get("temperature", 0.0)),
            max_tokens=merged.get("max_tokens"),
            timeout_s=float(merged.get("timeout_s", 120)),
            dim=merged.get("dim"),
            endpoint=(str(merged["endpoint"]).rstrip("/")
                      if merged.get("endpoint") else None),
            reasoning_effort=merged.get("reasoning_effort"),
            api_key_env=merged.get("api_key_env"),
        )


@dataclass
class Profile:
    """tier-S (laptop) or tier-L (GPU box)."""

    name: str
    endpoint: str
    backend: str = "ollama"
    max_loaded_models: int = 1
    keep_alive: str = "30s"
    fallback_profile: str | None = None
    lanes: dict[str, Lane] = field(default_factory=dict)
    # Name of the environment variable holding this endpoint's bearer token.
    # The NAME lives in the YAML; the key itself never does, because the YAML
    # is committed and a key in git is a key that has leaked.
    api_key_env: str | None = None

    @property
    def api_key(self) -> str | None:
        import os
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @classmethod
    def from_yaml(cls, name: str, raw: dict, defaults: dict) -> "Profile":
        endpoint = str(raw["endpoint"]).rstrip("/")
        backend = raw.get("backend") or _infer_backend(endpoint)
        lanes = {
            lane_name: Lane.from_yaml(lane_name, lane_raw, defaults)
            for lane_name, lane_raw in (raw.get("lanes") or {}).items()
        }
        return cls(
            name=name,
            endpoint=endpoint,
            backend=backend,
            api_key_env=raw.get("api_key_env"),
            max_loaded_models=int(raw.get("max_loaded_models", 1)),
            keep_alive=str(raw.get("keep_alive", "30s")),
            fallback_profile=raw.get("fallback_profile"),
            lanes=lanes,
        )

    def lane(self, name: str) -> Lane:
        if name not in self.lanes:
            raise ModelError(
                f"profile '{self.name}' has no lane '{name}'. "
                f"available: {sorted(self.lanes)}"
            )
        return self.lanes[name]


def _classify_host(endpoint: str) -> bool:
    """True when the endpoint is something we run ourselves (local or LAN)."""
    import ipaddress
    from urllib.parse import urlparse
    host = urlparse(endpoint).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1", "") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _infer_backend(endpoint: str) -> str:
    """Port 11434 means ollama. Assume anything else is OpenAI-compatible."""
    return "ollama" if endpoint.endswith(":11434") else "openai"


@dataclass
class RoutingClass:
    name: str
    lane: str
    hint: str = ""
    pre_tool: str | None = None
    retrieval: bool = True          # False for chitchat: never touch the corpus
    examples: list[str] = field(default_factory=list)


@dataclass
class Reply:
    """Result of one lane call, including latency and tokens for the UI."""

    text: str
    lane: str
    model: str
    profile: str
    latency_s: float
    thinking: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    fell_back: bool = False
    fell_back_why: str = ""            # what actually went wrong on the first try
    retried_for_truncation: int = 0    # 0=no, 1=budget raised, 2=thinking off

    def __str__(self) -> str:          # print(reply) gives the text directly
        return self.text

    @property
    def tok_per_s(self) -> float:
        return self.output_tokens / self.latency_s if self.latency_s else 0.0


# ---------------------------------------------------------------------------
# yaml loading
# ---------------------------------------------------------------------------


@dataclass
class Registry:
    active_profile: str
    defaults: dict
    profiles: dict[str, Profile]
    classes: list[RoutingClass]
    fallback_class: str
    path: Path
    retrieval: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = _YAML_PATH) -> "Registry":
        path = Path(path)
        raw = yaml.safe_load(path.read_text())
        defaults = raw.get("defaults") or {}
        profiles = {
            name: Profile.from_yaml(name, praw, defaults)
            for name, praw in (raw.get("profiles") or {}).items()
        }
        routing = raw.get("routing") or {}
        classes = [
            RoutingClass(
                name=c["name"],
                lane=c["lane"],
                hint=" ".join((c.get("hint") or "").split()),
                pre_tool=c.get("pre_tool"),
                retrieval=bool(c.get("retrieval", True)),
                examples=list(c.get("examples") or []),
            )
            for c in (routing.get("classes") or [])
        ]
        active = raw.get("active_profile") or next(iter(profiles))
        if active not in profiles:
            raise ModelError(f"active_profile '{active}' is not in profiles")
        return cls(
            active_profile=active,
            defaults=defaults,
            profiles=profiles,
            classes=classes,
            fallback_class=routing.get("fallback", "reason"),
            path=path,
            retrieval=raw.get("retrieval") or {},
        )

    def profile(self, name: str | None = None) -> Profile:
        name = name or self.active_profile
        if name not in self.profiles:
            raise ModelError(f"profile '{name}' is not in the YAML")
        return self.profiles[name]

    def klass(self, name: str) -> RoutingClass | None:
        for c in self.classes:
            if c.name == name:
                return c
        return None


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class Client:
    """
    Lane-based client. A caller never names a model, only a lane.

    Hot-swap: with max_loaded_models=1, the previous model is unloaded
    (keep_alive=0) before a different lane is called. On 8 GB of RAM this is
    the one thing that prevents swap thrashing.
    """

    def __init__(
        self,
        registry: Registry | None = None,
        profile: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.reg = registry or Registry.load()
        self.profile_name = profile or self.reg.active_profile
        self._http = client or httpx.Client()
        self._loaded: list[str] = []        # models currently resident in ollama

    # -- public ------------------------------------------------------------

    @property
    def profile(self) -> Profile:
        return self.reg.profile(self.profile_name)

    def chat(
        self,
        lane: str,
        prompt: str | Sequence[dict],
        *,
        system: str | None = None,
        images: Iterable[str | Path | bytes] = (),
        think: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        profile: str | None = None,
        _fell_back: bool = False,
        _retried: int = 0,
    ) -> Reply:
        """
        Talk to one lane. `prompt` accepts a string or a messages list.

        Passing think/temperature/max_tokens overrides the YAML setting;
        otherwise defaults.think=false applies - the 77x line.
        """
        prof = self.reg.profile(profile or self.profile_name)
        ln = prof.lane(lane)

        messages = self._build_messages(prompt, system, images)
        opts = {
            "think": ln.think if think is None else think,
            "temperature": ln.temperature if temperature is None else temperature,
            "max_tokens": ln.max_tokens if max_tokens is None else max_tokens,
        }

        t0 = time.perf_counter()
        try:
            # A lane pointed at its own engine runs on the backend that
            # endpoint implies, not the profile's. That is what lets one
            # profile mix vendors - the strongest reasoner and the strongest
            # vision model are not from the same company.
            backend = _infer_backend(ln.endpoint) if ln.endpoint else prof.backend
            if backend == "ollama":
                data = self._call_ollama(prof, ln, messages, opts)
            else:
                data = self._call_openai(prof, ln, messages, opts)
        except (httpx.HTTPError, ModelError) as exc:
            reply = self._try_fallback(
                prof, lane, prompt, system, images, think,
                temperature, max_tokens, exc,
            )
            return reply
        dt = time.perf_counter() - t0

        # With think=true the model reasons first, then writes the answer. If
        # the budget is spent reasoning, content comes back EMPTY - not an
        # exception. Measured: qwen3.5:2b needs ~2300 tokens. So two rungs:
        #   1. raise the budget to _THINK_RETRY_TOKENS
        #   2. still empty -> think=false, because an answer must come back
        # Nothing is worse in a demo than a blank box.
        if not data["text"].strip() and data.get("thinking", "").strip():
            if _retried == 0:
                return self.chat(
                    lane, prompt, system=system, images=images, think=True,
                    temperature=temperature,
                    max_tokens=max(
                        _THINK_RETRY_TOKENS, (opts["max_tokens"] or 1024) * 4
                    ),
                    profile=prof.name, _fell_back=_fell_back, _retried=1,
                )
            if _retried == 1:
                return self.chat(
                    lane, prompt, system=system, images=images, think=False,
                    temperature=temperature, max_tokens=opts["max_tokens"],
                    profile=prof.name, _fell_back=_fell_back, _retried=2,
                )

        return Reply(
            text=data["text"].strip(),
            lane=lane,
            model=ln.model,
            profile=prof.name,
            latency_s=dt,
            thinking=data.get("thinking", "").strip(),
            prompt_tokens=data.get("prompt_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            fell_back=_fell_back,
            retried_for_truncation=_retried,
        )

    def stream(
        self,
        lane: str,
        prompt: str | Sequence[dict],
        *,
        system: str | None = None,
        images: Iterable[str | Path | bytes] = (),
        think: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        profile: str | None = None,
    ) -> Iterator[dict]:
        """
        Token by token. Essential for the UI: the 2b lane takes 15-35s, and
        without streaming it looks like the app has hung.

        Yields:
            {"type": "thinking", "text": ...}   when think=true
            {"type": "token",    "text": ...}
            {"type": "done",     "reply": Reply}
        """
        prof = self.reg.profile(profile or self.profile_name)
        ln = prof.lane(lane)
        messages = self._build_messages(prompt, system, images)
        do_think = ln.think if think is None else think
        cap = ln.max_tokens if max_tokens is None else max_tokens

        options: dict[str, Any] = {
            "temperature": ln.temperature if temperature is None else temperature
        }
        if cap:
            options["num_predict"] = int(cap)

        if prof.backend != "ollama":
            # vLLM supports SSE too, but the demo runs on tier-S. Fall back to
            # a plain non-streaming call; the UI cannot tell the difference.
            r = self.chat(lane, prompt, system=system, images=images,
                          think=think, temperature=temperature,
                          max_tokens=max_tokens, profile=prof.name)
            yield {"type": "token", "text": r.text}
            yield {"type": "done", "reply": r}
            return

        self._ensure_room(prof, ln.model)
        payload = {
            "model": ln.model, "messages": messages, "stream": True,
            "think": bool(do_think), "keep_alive": prof.keep_alive,
            "options": options,
        }

        t0 = time.perf_counter()
        text_parts: list[str] = []
        think_parts: list[str] = []
        ptok = otok = 0

        with self._http.stream(
            "POST", f"{prof.endpoint}/api/chat", json=payload,
            timeout=ln.timeout_s,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = body.get("message") or {}
                if msg.get("thinking"):
                    think_parts.append(msg["thinking"])
                    yield {"type": "thinking", "text": msg["thinking"]}
                if msg.get("content"):
                    text_parts.append(msg["content"])
                    yield {"type": "token", "text": msg["content"]}
                if body.get("done"):
                    ptok = body.get("prompt_eval_count", 0)
                    otok = body.get("eval_count", 0)

        self._mark_loaded(ln.model)
        full = "".join(text_parts).strip()
        thinking = "".join(think_parts).strip()

        # the same truncation trap as chat() - it can hit in streaming too
        if not full and thinking:
            r = self.chat(lane, prompt, system=system, images=images,
                          think=True, temperature=temperature,
                          max_tokens=max(_THINK_RETRY_TOKENS, (cap or 1024) * 4),
                          profile=prof.name)
            yield {"type": "token", "text": r.text}
            yield {"type": "done", "reply": r}
            return

        yield {
            "type": "done",
            "reply": Reply(
                text=full, lane=lane, model=ln.model, profile=prof.name,
                latency_s=time.perf_counter() - t0, thinking=thinking,
                prompt_tokens=ptok, output_tokens=otok,
            ),
        }

    def classify(self, text: str, *, profile: str | None = None) -> RoutingClass:
        """
        Ask the router lane which class this question belongs to.

        32 tokens with think=false, so roughly half a second. If the model
        returns nonsense, routing.fallback is applied silently.
        """
        if not self.reg.classes:
            raise ModelError("routing.classes is empty in the YAML")

        names = [c.name for c in self.reg.classes]
        catalogue = "\n".join(f"- {c.name}: {c.hint}" for c in self.reg.classes)
        # Few-shot: the 0.8b model collapses onto the first class even after
        # reading the hints, but not after seeing examples. These come from YAML.
        shots = "\n".join(
            f'"{ex}" -> {c.name}'
            for c in self.reg.classes for ex in c.examples
        )
        system = (
            "You are a classifier. Put the user input into exactly one class.\n"
            f"Classes:\n{catalogue}\n"
            + (f"\nExamples:\n{shots}\n" if shots else "")
            + '\nReturn JSON only, nothing else: {"class": "<one of: '
            + ", ".join(names)
            + '>"}'
        )
        reply = self.chat(
            "router", text, system=system, think=False, profile=profile
        )
        return self.reg.klass(self._parse_class(reply.text, names)) or self.reg.klass(
            self.reg.fallback_class
        ) or self.reg.classes[0]

    def embed(
        self, texts: str | Sequence[str], *, profile: str | None = None
    ) -> list[list[float]]:
        """Vectors from nomic-embed-text, or whatever the embed lane names."""
        prof = self.reg.profile(profile or self.profile_name)
        ln = prof.lane("embed")
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []

        # A lane with its own endpoint runs there, on the backend that endpoint
        # implies, whatever the profile is set to.
        endpoint = ln.endpoint or prof.endpoint
        backend = _infer_backend(endpoint) if ln.endpoint else prof.backend

        self._ensure_room(prof, ln.model)
        if backend == "ollama":
            r = self._http.post(
                f"{endpoint}/api/embed",
                json={"model": ln.model, "input": items,
                      "keep_alive": prof.keep_alive},
                timeout=ln.timeout_s,
            )
            r.raise_for_status()
            vecs = r.json()["embeddings"]
        else:
            r = self._http.post(
                f"{endpoint}/v1/embeddings",
                json={"model": ln.model, "input": items},
                timeout=ln.timeout_s,
            )
            r.raise_for_status()
            vecs = [d["embedding"] for d in r.json()["data"]]

        if ln.dim and vecs and len(vecs[0]) != ln.dim:
            raise ModelError(
                f"embed dim mismatch: YAML says {ln.dim}, got {len(vecs[0])}. "
                "The index will have to be rebuilt."
            )
        return vecs

    def health(self) -> dict:
        """For the UI panel: which profile, which models, and is it up."""
        prof = self.profile
        out: dict[str, Any] = {
            "profile": prof.name,
            "endpoint": prof.endpoint,
            "backend": prof.backend,
            "max_loaded_models": prof.max_loaded_models,
            "lanes": {n: l.model for n, l in prof.lanes.items()},
            "loaded": list(self._loaded),
        }
        try:
            if prof.backend == "ollama":
                r = self._http.get(f"{prof.endpoint}/api/tags", timeout=5)
                r.raise_for_status()
                have = {m["name"] for m in r.json().get("models", [])}
                have |= {n.split(":")[0] for n in have}
                out["up"] = True
                out["missing"] = sorted(
                    {l.model for l in prof.lanes.values()}
                    - have
                    - {m.split(":")[0] for m in have}
                )
            else:
                r = self._http.get(f"{prof.endpoint}/v1/models", timeout=5)
                r.raise_for_status()
                out["up"] = True
                out["missing"] = []
        except httpx.HTTPError as exc:
            out["up"] = False
            out["error"] = str(exc)
        return out

    def unload_all(self) -> None:
        """Free RAM mid-demo by unloading everything."""
        prof = self.profile
        if prof.backend != "ollama":
            self._loaded.clear()
            return
        for model in list(self._loaded):
            self._unload(prof, model)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- backends ----------------------------------------------------------

    def _call_ollama(
        self, prof: Profile, ln: Lane, messages: list[dict], opts: dict
    ) -> dict:
        self._ensure_room(prof, ln.model)
        options: dict[str, Any] = {"temperature": opts["temperature"]}
        if opts["max_tokens"]:
            options["num_predict"] = int(opts["max_tokens"])

        payload = {
            "model": ln.model,
            "messages": messages,
            "stream": False,
            "think": bool(opts["think"]),
            "keep_alive": prof.keep_alive,
            "options": options,
        }
        r = self._http.post(
            f"{prof.endpoint}/api/chat", json=payload, timeout=ln.timeout_s
        )
        r.raise_for_status()
        body = r.json()
        msg = body.get("message") or {}
        self._mark_loaded(ln.model)
        return {
            "text": msg.get("content", ""),
            "thinking": msg.get("thinking", "") or "",
            "prompt_tokens": body.get("prompt_eval_count", 0),
            "output_tokens": body.get("eval_count", 0),
        }

    def _call_openai(
        self, prof: Profile, ln: Lane, messages: list[dict], opts: dict
    ) -> dict:
        """vLLM / TGI. `think` is not a param here; the chat template handles it."""
        payload: dict[str, Any] = {
            "model": ln.model,
            "messages": self._to_openai_messages(messages),
            "temperature": opts["temperature"],
            "stream": False,
        }
        if opts["max_tokens"]:
            payload["max_tokens"] = int(opts["max_tokens"])
        if ln.reasoning_effort:
            # DeepSeek's own switch. Sent only when a lane asks for it.
            payload["reasoning_effort"] = ln.reasoning_effort
        elif not opts["think"] and _classify_host(ln.endpoint or prof.endpoint):
            # chat_template_kwargs is a vLLM extension for the Qwen chat
            # template. It is NOT part of the OpenAI API, and a hosted vendor
            # rejects the whole request over it: Gemini answered 400 Bad
            # Request for every call until this was gated, and the failure was
            # invisible because the lane quietly fell back to the local model.
            #
            # So it goes only to an engine on this machine or this LAN, which
            # is where vLLM actually runs. A vendor-specific extension has no
            # business on a vendor's own endpoint.
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        # A self-hosted vLLM pod needs no auth; a hosted endpoint does. The
        # header is sent only when the named environment variable is actually
        # set, so a missing key fails as an auth error from the server rather
        # than as a confusing 404 here.
        headers = {}
        key = ln.api_key or prof.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"

        r = self._http.post(
            f"{ln.endpoint or prof.endpoint}/v1/chat/completions",
            json=payload,
            headers=headers or None,
            timeout=httpx.Timeout(ln.timeout_s, connect=_CONNECT_TIMEOUT),
        )
        r.raise_for_status()
        body = r.json()
        choice = (body.get("choices") or [{}])[0].get("message") or {}
        usage = body.get("usage") or {}
        return {
            "text": choice.get("content") or "",
            "thinking": choice.get("reasoning_content") or "",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }

    # -- hot swap ----------------------------------------------------------

    def _ensure_room(self, prof: Profile, model: str) -> None:
        """
        Enforce max_loaded_models. Ollama manages this itself, but on 8 GB it
        will happily hold both models and end up in swap, so we unload
        explicitly.
        """
        if prof.backend != "ollama" or model in self._loaded:
            return
        while len(self._loaded) >= max(1, prof.max_loaded_models):
            self._unload(prof, self._loaded[0])

    def _unload(self, prof: Profile, model: str) -> None:
        try:
            self._http.post(
                f"{prof.endpoint}/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0},
                timeout=30,
            )
        except httpx.HTTPError:
            pass                                  # unloading is best-effort
        if model in self._loaded:
            self._loaded.remove(model)

    def _mark_loaded(self, model: str) -> None:
        if model in self._loaded:
            self._loaded.remove(model)
        self._loaded.append(model)                # LRU: oldest is evicted first

    # -- fallback ----------------------------------------------------------

    def _try_fallback(
        self, prof, lane, prompt, system, images, think,
        temperature, max_tokens, exc,
    ) -> Reply:
        """
        tier-L timed out or is down: retry the same call on fallback_profile.

        The reason travels with the reply. Without it a misconfigured tier
        degrades to the local model in total silence - a wrong model name and
        an expired key look identical to a working system, and the only clue
        is that answers got slower. Diagnosing the Gemini lane cost two rounds
        of guessing precisely because this was swallowed.
        """
        target = prof.fallback_profile
        why = f"{type(exc).__name__}: {exc}"[:200]
        if not target or target == prof.name:
            raise ModelError(
                f"lane '{lane}' failed on profile '{prof.name}': {exc}"
            ) from exc
        reply = self.chat(
            lane, prompt, system=system, images=images, think=think,
            temperature=temperature, max_tokens=max_tokens,
            profile=target, _fell_back=True,
        )
        reply.fell_back_why = f"{prof.name}/{lane} -> {why}"
        return reply

    # -- helpers -----------------------------------------------------------

    def _build_messages(
        self,
        prompt: str | Sequence[dict],
        system: str | None,
        images: Iterable[str | Path | bytes],
    ) -> list[dict]:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        else:
            messages = [dict(m) for m in prompt]
        if system:
            messages = [{"role": "system", "content": system}] + messages

        encoded = [_b64(i) for i in images]
        if encoded:
            for m in reversed(messages):
                if m.get("role") == "user":
                    m["images"] = list(m.get("images", [])) + encoded
                    break
        return messages

    @staticmethod
    def _to_openai_messages(messages: list[dict]) -> list[dict]:
        """Convert ollama's `images` key into OpenAI content parts."""
        out = []
        for m in messages:
            imgs = m.get("images")
            if not imgs:
                out.append({k: v for k, v in m.items() if k != "images"})
                continue
            parts: list[dict] = [{"type": "text", "text": m.get("content", "")}]
            parts += [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b}"}}
                for b in imgs
            ]
            out.append({"role": m["role"], "content": parts})
        return out

    @staticmethod
    def _parse_class(text: str, names: list[str]) -> str:
        """Prefer JSON; else find a bare class name; else return ""."""
        m = re.search(r"\{.*?\}", text, re.S)
        if m:
            try:
                val = json.loads(m.group(0)).get("class", "")
                if val in names:
                    return val
            except json.JSONDecodeError:
                pass
        low = text.lower()
        for n in names:                            # first one that appears
            if re.search(rf"\b{re.escape(n)}\b", low):
                return n
        return ""


_CONNECT_TIMEOUT = 2.0      # "if the GPU box does not answer in 2s, drop to tier-S"
_THINK_RETRY_TOKENS = 4096  # measured: qwen3.5:2b needs ~2300, plus margin


def _b64(img: str | Path | bytes) -> str:
    if isinstance(img, bytes):
        return base64.b64encode(img).decode()
    p = Path(img)
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return str(img)                                # assume it is already base64


# ---------------------------------------------------------------------------
# selftest — `python -m core.llm`
# ---------------------------------------------------------------------------

def _selftest() -> int:
    from core import airgap
    airgap.seal()

    c = Client()
    h = c.health()
    print(f"profile={h['profile']}  backend={h['backend']}  up={h['up']}")
    if not h["up"]:
        print(f"  ollama down: {h.get('error')}")
        return 1
    if h["missing"]:
        print(f"  MISSING models: {h['missing']}")
        return 1
    print(f"lanes: {h['lanes']}\n")

    print(f"{'lane':8} {'model':20} {'s':>7} {'tok':>5} {'tok/s':>7}  reply")
    print("-" * 78)
    probes = [
        ("router", "summarise the inspection report for TK-4102"),
        ("code", "Python one-liner: sum of 1..100. Code only."),
        ("reason", "In one line: why are pressure vessels inspected?"),
    ]
    for lane, q in probes:
        r = c.chat(lane, q)
        head = r.text.replace("\n", " ")[:30]
        print(f"{lane:8} {r.model:20} {r.latency_s:7.2f} {r.output_tokens:5} "
              f"{r.tok_per_s:7.1f}  {head}")

    v = c.embed(["tank TK-4102 shell thickness"])
    print(f"\nembed dim = {len(v[0])}")

    t0 = time.perf_counter()
    k = c.classify("this is a piping diagram, I need to trace a valve")
    print(f"classify -> {k.name} (lane={k.lane}, pre_tool={k.pre_tool}) "
          f"in {time.perf_counter() - t0:.2f}s")

    print("\n" + airgap.MONITOR.report())
    c.unload_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
