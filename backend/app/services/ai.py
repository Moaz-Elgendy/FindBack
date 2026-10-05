"""Provider-agnostic chat + embedding calls: Groq, Gemini, OpenAI.

Built on ``httpx`` (already a dependency) rather than the OpenAI SDK, because
every provider we support speaks the same REST/JSON dialect and httpx lets us
send exactly the body each one wants instead of the body the SDK's pinned type
hints allow. The ``openai`` package is no longer installed.

Endpoints verified against provider docs (2026-09):

* Groq   ``POST https://api.groq.com/openai/v1/chat/completions``
         ``POST https://api.groq.com/openai/v1/embeddings``  (nomic-embed-text-v1_5)
* Gemini ``POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions``
         ``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent``
         ``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents``
* OpenAI ``POST https://api.openai.com/v1/{chat/completions,embeddings}`` and any
         OpenAI-compatible server via ``OPENAI_BASE_URL``.

Request and response handling is defensive on purpose: providers differ in
``choices[0].message.content`` vs ``candidates[0].content.parts[].text``, return
``null`` content, wrap JSON in markdown fences, reject a parameter the docs say
is supported, or answer a 200 with an HTML error page. Each case has a branch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from contextvars import ContextVar
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app import env

log = logging.getLogger("findback.ai")
CHAT_USAGE = ContextVar("brief_chat_usage", default=None)

# Test seams only: unit tests install an httpx.MockTransport here and zero the
# backoff so the retry, rate-limit, and request-shape self-heal paths run
# without waiting on real delays or reaching the network. Production leaves all
# three at these values (transport=None means httpx's default).
_TRANSPORT: httpx.AsyncBaseTransport | None = None
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0

GROQ_BASE = "https://api.groq.com/openai/v1"
OPENAI_BASE = "https://api.openai.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_OPENAI_BASE = f"{GEMINI_BASE}/openai"

# Defaults are only a starting point: models are retired often, so every one of
# these is overridable in .env and a rejected id is reported verbatim.
CHAT_MODEL_DEFAULTS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-4o-mini",
    "openai_compatible": "gpt-4o-mini",
}
# gemini-embedding-001 accepts 128-3072 dims and recommends 768/1536/3072, so
# 1536 keeps the existing pgvector columns usable. Groq's nomic model is ~137
# dims and rejects empty input, which is why it is not the default provider.
EMBED_MODEL_DEFAULTS = {
    "gemini": "gemini-embedding-001",
    "openai": "text-embedding-3-small",
    "openai_compatible": "text-embedding-3-small",
    "groq": "nomic-embed-text-v1_5",
}
CHAT_KEY_ENVS = {
    "groq": ("GROQ_API_KEY",),
    # GEMINI_API_KEY and GOOGLE_API_KEY are two spellings of ONE Google AI Studio
    # key, not two different credentials. Both feed the same `gemini` provider,
    # for chat and for embeddings alike; nothing reads either for another
    # purpose. GEMINI_API_KEY is the documented name and is checked first;
    # GOOGLE_API_KEY is a quiet fallback so an existing .env keeps working.
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "openai_compatible": ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY"),
}
# One environment variable per model role. There are exactly two roles today:
# the chat/text model (brief extraction, plus the connectivity probe) and the
# embedding model. Nothing else calls a model.
CHAT_MODEL_ENV = "AI_MODEL"
# The name this role had before it was split out by role. Still read, and still
# wins over nothing, so a .env written against it keeps the model it asked for.
CHAT_MODEL_LEGACY_ENV = "EXTRACTOR_MODEL"
EMBEDDING_MODEL_ENV = "EMBEDDING_MODEL"
# Order used when AI_PROVIDER is unset and several keys are present.
AUTODETECT_ORDER = ("groq", "gemini", "openai", "openai_compatible")
# Providers whose embedding endpoint is worth calling by default.
EMBEDDING_CAPABLE = ("gemini", "openai", "openai_compatible", "groq")

# Request fields we may silently drop if the provider rejects them. Only
# quality-of-output knobs belong here: dropping outputDimensionality would
# change the vector width and break the pgvector column instead of the call.
DROPPABLE = {
    "response_format", "temperature", "top_p", "seed", "stop",
    "max_tokens", "max_completion_tokens", "encoding_format", "frequency_penalty",
    "dimensions", "taskType",
}
# How a provider may name a rejected field inside its error text. Gemini's
# OpenAI-compatible layer rejects bodies using its own field names, e.g.
# "Unknown name \"responseMimeType\"" - both spellings land on response_format.
# Keys here are compared against the provider message with spaces and
# underscores removed (that is how "response_format", "response format", and
# "responseFormat" all become one comparable string), so they stay underscore-free.
PARAM_ALIASES = {
    "responsedmimetype": "response_format",
    "responsemime": "response_format",
    "responseformat": "response_format",
    "maxoutputtokens": "max_tokens",
    "maxtokencount": "max_tokens",
    "maxcompletiontokens": "max_completion_tokens",
    "temperature": "temperature",
    # gemini-embedding-2 dropped task_type entirely; a 400 naming it is the
    # signal to send document/query vectors the same way instead.
    "tasktype": "taskType",
}
# Normalized lookup for the generic quoted-name scan, keeping the real request
# key (taskType is the only camelCase one) so the caller can delete it from body.
DROPPABLE_CANONICAL = {name.lower().replace("_", ""): name for name in DROPPABLE}
GEMINI_FINISH_REASONS = {
    "SAFETY": "Gemini blocked the request for safety reasons",
    "BLOCKLIST": "Gemini blocked the prompt",
    "PROHIBITED_CONTENT": "Gemini blocked prohibited content",
    "RECITATION": "Gemini refused to avoid reciting copyrighted text",
    "SPII": "Gemini blocked the request: sensitive personal information",
    "NO_IMAGE": "Gemini returned no content",
}
# Statuses worth another attempt: rate limits, timeouts, and the provider's own
# failures. Any other 4xx is a bug in our request, so retrying it only burns
# quota and delays the item; those raise on the first response with a hint.
RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class AIError(Exception):
    """Base class. Carries a short, log-friendly reason plus the provider name."""

    def __init__(self, message: str, provider: str = ""):
        super().__init__(message)
        self.provider = provider


class AIConfigError(AIError):
    """Missing key, placeholder key, or otherwise unusable configuration."""


class AIUnreachableError(AIError):
    """DNS, connection, or timeout failure - the endpoint never answered."""


class AIHTTPError(AIError):
    """The provider answered with a non-2xx status."""

    def __init__(self, message: str, provider: str = "", status: int = 0,
                 retryable: bool = False):
        super().__init__(message, provider)
        self.status = status
        self.retryable = retryable
        self.hint = ""


class AIProtocolError(AIError):
    """A 2xx body we could not read: not JSON, or an unknown response shape."""


class AIJSONError(AIError):
    """The model's text was not valid JSON, or not a JSON object."""


@dataclass(frozen=True)
class ChatConfig:
    provider: str
    api_key: str
    model: str
    url: str

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    api_key: str
    model: str
    style: str      # "openai" | "gemini_native"
    url: str        # full url when style=openai, model resource when gemini_native
    dims: int
    normalize: bool

    @property
    def headers(self) -> dict:
        if self.style == "gemini_native":
            # Google also accepts ?key=; a header keeps the key out of URLs and logs.
            return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @property
    def label(self) -> str:
        """provider:model, for log lines and the startup summary."""
        return f"{self.provider}:{self.model}"


def _chat_base_url(provider: str) -> str:
    if provider == "groq":
        return GROQ_BASE
    if provider == "gemini":
        return GEMINI_OPENAI_BASE
    if provider == "openai_compatible":
        return env.get("OPENAI_BASE_URL").rstrip("/") or OPENAI_BASE
    return OPENAI_BASE


def _resolve_provider(explicit: str) -> str:
    """Map AI_PROVIDER/EMBEDDING_PROVIDER to a provider name, or detect by key."""
    name = explicit.strip().lower().replace("-", "_")
    if name in ("", "auto"):
        for candidate in AUTODETECT_ORDER:
            _, value = env.first_env(*CHAT_KEY_ENVS[candidate])
            if value:
                return candidate
        return ""
    if name == "google":
        name = "gemini"
    if name in ("openai_compatible", "ollama", "openrouter"):
        name = "openai_compatible"
    if name not in env.PROVIDERS:
        log.warning("[ai] unknown provider %r; expected one of: %s",
                    explicit, ", ".join(env.PROVIDERS))
        return ""
    return name


def chat_model(provider: str) -> str:
    """The chat/text model for `provider`, and where that answer came from.

    Resolution order, and nothing outside it decides:

      1. `AI_MODEL` in the environment
      2. `EXTRACTOR_MODEL`, the older name for the same role
      3. `CHAT_MODEL_DEFAULTS[provider]`, so an unset environment behaves
         exactly as it did before this variable existed

    A rejected id is reported verbatim by the transport rather than corrected
    here, so pinning a newer Flash version is a pure .env change.
    """
    return (env.get(CHAT_MODEL_ENV)
            or env.get(CHAT_MODEL_LEGACY_ENV)
            or CHAT_MODEL_DEFAULTS[provider])


def embedding_model(provider: str) -> str:
    """The embedding model for `provider`: `EMBEDDING_MODEL`, else the default.

    Changing this invalidates every stored vector -- see the warning in
    .env.example. Reuse in tasks._reuse_source already refuses to hand over
    vectors stamped with a different model, but search itself has no such
    filter, so old and new vectors end up scored against the same query vector.
    """
    return env.get(EMBEDDING_MODEL_ENV) or EMBED_MODEL_DEFAULTS[provider]


def chat_config() -> ChatConfig | None:
    """Resolve the chat provider, or None when no usable key is configured."""
    provider = _resolve_provider(env.get("AI_PROVIDER"))
    if not provider:
        return None
    _, api_key = env.first_env(*CHAT_KEY_ENVS[provider])
    if not api_key:
        log.warning("[ai] AI_PROVIDER=%s but none of %s holds a real key; "
                    "using the offline heuristic extractor instead",
                    provider, ", ".join(CHAT_KEY_ENVS[provider]))
        return None
    model = chat_model(provider)
    if provider in ("groq", "gemini") and model.lower().startswith("gpt-"):
        log.warning("[ai] %s=%r looks like an OpenAI id, which %s "
                    "will reject with a 400 - set %s explicitly",
                    CHAT_MODEL_ENV, model, provider, CHAT_MODEL_ENV)
    return ChatConfig(provider, api_key, model, f"{_chat_base_url(provider)}/chat/completions")


def embedding_config() -> EmbeddingConfig | None:
    """Resolve the embedding provider, or None when embeddings are unavailable.

    Chat and embeddings resolve separately on purpose: no Groq embedding model
    matches our 1536-dimension column, so a Groq-only setup should let Gemini
    (or OpenAI) do the embedding, or run with embeddings disabled - search.py
    already degrades to BM25/keyword recall when embed_text returns None.
    """
    # Only an explicit EMBEDDING_PROVIDER overrides detection: handing "" (or
    # "auto") to _resolve_provider would key-detect Groq, whose nomic model is
    # far narrower than the column. _embeddings_autodetect prefers a
    # vector-capable provider key instead of running without vectors.
    explicit = env.get("EMBEDDING_PROVIDER").strip().lower()
    provider = _resolve_provider(explicit) if explicit not in ("", "auto") else ""
    if not provider:
        provider = _embeddings_autodetect()
    if not provider:
        return None
    if provider not in EMBEDDING_CAPABLE:
        log.warning("[ai] %s has no embeddings endpoint; vector search stays off", provider)
        return None
    _, api_key = env.first_env(*CHAT_KEY_ENVS[provider])
    if not api_key:
        log.warning("[ai] embedding provider %s has no key (checked %s); vector "
                    "search stays off, keyword search still works",
                    provider, ", ".join(CHAT_KEY_ENVS[provider]))
        return None
    model = embedding_model(provider)
    dims = env.EMBEDDING_DIMS
    style = "gemini_native" if provider == "gemini" else "openai"
    if style == "gemini_native":
        # Native endpoint rather than the OpenAI-compatible one: outputDimen-
        # sionality is only documented there, and a wrong width is a hard
        # database error rather than a warning.
        url = f"{GEMINI_BASE}/models/{model}"
    else:
        url = f"{_chat_base_url(provider)}/embeddings"
    # Truncated Gemini vectors (dims < 3072) are documented as needing manual
    # renormalisation before cosine similarity is meaningful.
    normalize = env.get_bool("EMBEDDING_NORMALIZE", True) and style == "gemini_native" and dims < 3072
    return EmbeddingConfig(provider, api_key, model, style, url, dims, normalize)


def _embeddings_autodetect() -> str:
    """Pick an embedding provider when EMBEDDING_PROVIDER is unset."""
    chat_provider = _resolve_provider(env.get("AI_PROVIDER"))
    if chat_provider in ("gemini", "openai", "openai_compatible"):
        return chat_provider
    # A Groq-only chat setup still has no embedding model wide enough for our
    # column, so look for a separate embedding-capable key rather than running
    # without vectors: the intended default is Groq chat + Gemini embeddings.
    for candidate in EMBEDDING_CAPABLE:
        if candidate == chat_provider or candidate == "groq":
            continue
        _, value = env.first_env(*CHAT_KEY_ENVS[candidate])
        if value:
            return candidate
    return ""


def _error_message(response: httpx.Response) -> str:
    """Pull a human-readable message out of any provider's error envelope.

    Shapes seen in the wild:
      {"error": {"message": "...", "type": "...", "code": "model_not_found"}}
      {"error": {"code": 400, "message": "...", "status": "INVALID_ARGUMENT"}}
      {"error": "..."}   {"message": "..."}   {"detail": "..."}
      an HTML body from a proxy, which has no JSON at all.
    """
    body = (response.text or "").strip()
    try:
        data = response.json()
    except ValueError:
        return body[:400] or f"HTTP {response.status_code} with an unparseable body"
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            nested = err.get("error")
            if isinstance(nested, dict):
                err = nested
            for key in ("message", "msg", "detail", "reason"):
                value = err.get(key)
                if isinstance(value, str) and value.strip():
                    status = err.get("status") or err.get("code") or ""
                    return f"{value.strip()} [{status}]" if status else value.strip()
        elif isinstance(err, str) and err.strip():
            return err.strip()[:400]
        for key in ("message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:400]
    return json.dumps(data)[:400] if data else f"HTTP {response.status_code}"


def _rejected_param(message: str) -> str:
    """Which request field, if any, the provider complained about."""
    low = message.lower().replace(" ", "").replace("_", "")
    for alias, field in PARAM_ALIASES.items():
        if alias in low and field in DROPPABLE:
            return field
    # Generic phrasing: "Unexpected field 'foo'" / 'Unrecognized key: "top_p"'.
    for match in re.finditer(r"['\"](\w+)['\"]", message):
        field = DROPPABLE_CANONICAL.get(match.group(1).lower().replace("_", ""))
        if field:
            return field
    return ""


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Exponential backoff with jitter, honouring a provider's Retry-After."""
    if response is not None:
        raw = response.headers.get("retry-after", "").strip()
        if raw:
            try:
                return min(float(raw), 20.0)
            except ValueError:
                pass  # An HTTP-date; retrying with the default backoff is fine.
    return min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) * (0.8 + 0.4 * random.random())


async def _wait_for_rate_limit(provider: str) -> None:
    """Block until this provider's token bucket allows another call.

    Phase 7. Waiting here rather than retrying after a 429 keeps us inside the
    published quota, which is cheaper for everyone than being throttled. The
    sleep is yielded to the event loop so other coroutines keep running.
    """
    import asyncio as _asyncio

    from app.services.limits import rate_limiter_for

    bucket = rate_limiter_for(provider)
    while not bucket.try_acquire():
        await _asyncio.sleep(min(0.05, 1.0 / bucket.rate_per_sec))


async def _post_json(url: str, headers: dict, payload: dict, *, provider: str,
                     attempts: int | None = None, timeout: float | None = None) -> dict:
    """POST JSON and return the decoded object body, retrying what can recover.

    Handles four distinct failure families, which the old single try/except
    collapsed into one unhelpful log line: unreachable transport, retriable
    status, a request body the provider refuses, and an unreadable body.
    """
    attempts = attempts or env.AI_MAX_ATTEMPTS
    timeout = timeout or env.AI_TIMEOUT
    dropped: set[str] = set()
    last_error: AIError | None = None
    response: httpx.Response | None = None

    for attempt in range(1, attempts + 1):
        body = {k: v for k, v in payload.items() if k not in dropped}
        response = None  # never let a prior attempt's Retry-After leak forward
        # Phase 7: stay inside the provider's published quota by waiting for a
        # token BEFORE sending, instead of sending and being rejected with 429.
        # The bucket is per provider; the same call shapes every AI request.
        await _wait_for_rate_limit(provider)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=_TRANSPORT) as client:
                response = await client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            kind = type(exc).__name__
            last_error = AIUnreachableError(
                f"{provider} is unreachable at {url} ({kind}: {exc})", provider)
            log.warning("[ai] %s (attempt %d/%d)", last_error, attempt, attempts)
        else:
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    last_error = AIProtocolError(
                        f"{provider} replied 200 but the body is not JSON: "
                        f"{(response.text or '')[:200]!r} ({exc})", provider)
                    log.warning("[ai] %s (attempt %d/%d)", last_error, attempt, attempts)
                else:
                    if not isinstance(data, dict):
                        raise AIProtocolError(
                            f"{provider} returned {type(data).__name__}, expected a JSON object",
                            provider)
                    if data.get("error"):  # some gateways 200-wrap an error envelope
                        raise AIProtocolError(f"{provider} error envelope: {_error_message(response)}",
                                              provider)
                    return data
            else:
                message = _error_message(response)
                param = _rejected_param(message) if response.status_code == 400 else ""
                if param and param in body and len(dropped) < 3:
                    dropped.add(param)
                    log.warning("[ai] %s rejected %s=%r; retrying without it",
                                provider, param, message[:160])
                    continue  # Request-shape fix, no backoff needed.
                retryable = response.status_code in RETRY_STATUSES
                last_error = AIHTTPError(
                    f"{provider} returned HTTP {response.status_code}: {message}",
                    provider, response.status_code, retryable)
                if not retryable:
                    raise _with_hint(last_error, body, url)
                log.warning("[ai] %s (attempt %d/%d)", last_error, attempt, attempts)

        if attempt < attempts:
            await asyncio.sleep(_retry_delay(response, attempt))

    raise last_error or AIError(f"{provider} request failed", provider)


def _with_hint(error: AIHTTPError, body: dict, url: str) -> AIHTTPError:
    """Attach an actionable hint to the statuses a developer can act on."""
    status, message = error.status, str(error).lower()
    if status in (401, 403):
        hint = (f"the API key for {error.provider} was rejected - check the key in .env "
                f"and that it is not still the .env.example placeholder")
    elif status == 404 or "model_not_found" in message or "is not found" in message:
        model = body.get("model", "?")
        hint = (f"model {model!r} is not available for {error.provider}; list valid ids with "
                f"`python scripts/check_ai.py --list-models` and set EXTRACTOR_MODEL")
    elif status == 429:
        hint = "rate limited; raise AI_MAX_RETRIES or lower ingest concurrency"
    elif status in (400, 422):
        hint = ("the request body was refused; the provider message above names the field "
                "(set AI_DEBUG=1 to log the exact body)")
    else:
        hint = f"see {url}"
    error.hint = hint
    # Appended to the message so the fix survives any str() of the exception:
    # log lines, the extractor's warning, and CI output all show it unchanged.
    error.args = (f"{error.args[0]} | fix: {hint}",)
    return error

# --- response parsing: providers do not agree on the shape -------------------

def _parts_text(content) -> str:
    """Flatten OpenAI content-parts or Gemini parts arrays into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                # OpenAI: {"type": "text", "text": ...}; Gemini: {"text": ...}
                value = part.get("text") or part.get("content") or ""
                if isinstance(value, str):
                    chunks.append(value)
        return "".join(chunks)
    return "" if content is None else str(content)


def completion_text(data: dict, provider: str) -> str:
    """Read the model's text out of an OpenAI *or* Gemini-native response.

    Both response dialects are handled because Gemini's OpenAI-compatible layer
    is not the only path we use, and gateways have been observed mixing the two.
    """
    if "candidates" in data:  # Gemini native
        block = data.get("promptFeedback") or {}
        if block.get("blockReason"):
            raise AIProtocolError(
                f"gemini blocked the prompt ({block['blockReason']})", provider)
        chunks = []
        for candidate in data.get("candidates") or []:
            reason = (candidate.get("finishReason") or "").upper()
            text = _parts_text((candidate.get("content") or {}).get("parts"))
            if not text and reason in GEMINI_FINISH_REASONS:
                raise AIProtocolError(
                    f"{GEMINI_FINISH_REASONS[reason]} (finishReason={reason})", provider)
            chunks.append(text)
        return "".join(chunks).strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        text = _parts_text(message.get("content"))
        if not text:
            # Reasoning models can spend the whole budget on reasoning_content,
            # and a refusal arrives as null content - distinguish both from "empty".
            reason = first.get("finish_reason") or ""
            if message.get("refusal"):
                raise AIProtocolError(f"{provider} refused the request: {message['refusal']}",
                                      provider)
            if reason == "length":
                raise AIProtocolError(
                    f"{provider} hit the token limit before producing an answer; "
                    f"raise EXTRACTOR_MAX_TOKENS (now {env.EXTRACTOR_MAX_TOKENS})", provider)
            if not reason and not message:
                raise AIProtocolError(
                    f"{provider} response has choices[0] without a message: {str(first)[:200]}",
                    provider)
        return text.strip()

    for key in ("output_text", "text", "completion"):  # other gateway dialects
        if isinstance(data.get(key), str):
            return data[key].strip()
    raise AIProtocolError(
        f"{provider} response has no text content; top-level keys: {sorted(data)[:8]}",
        provider)


_THINK_RE = re.compile(r"<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>",
                       re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.+?)```", re.S)


def _clean_json_text(text: str) -> str:
    """Strip the prose wrappers a model puts around a JSON object."""
    text = _THINK_RE.sub("", text)          # reasoning blocks around the answer
    text = text.replace("\ufeff", "").replace("\xa0", " ")
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)              # the object lives inside a code fence
    return text.strip()



def _balanced_slice(text: str) -> str:
    """Return the first balanced {...} run, or the truncated remainder."""
    start = text.find("{")
    if start < 0:
        return ""
    depth, in_string, escape = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]  # truncated mid-object: caller closes it


def _close_truncated(fragment: str) -> str:
    """Make a completion cut off by a token limit parseable where possible."""
    # Drop a trailing dangling key ("a": 1, "b": ) or incomplete value.
    fragment = re.sub(r',\s*"[^"]*"\s*:\s*(?:[^,}\]"]*|"[^"]*)?$', "", fragment)
    fragment = re.sub(r',\s*$', "", fragment)
    stack: list[str] = []
    in_string, escape = False, False
    for char in fragment:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack:
            stack.pop()

    if in_string:
        fragment += '"'  # the cut landed inside a string value
    return fragment + "".join(reversed(stack)) if stack else fragment


def parse_json_object(text: str, provider: str = "") -> dict:
    """Coerce a completion into a dict, tolerating real-world model output.

    Ordered from most to least invasive, so a well-formed answer is never
    rewritten: plain parse, the first balanced object inside surrounding prose,
    trailing-comma removal, then closing an object truncated by a token limit.
    """
    cleaned = _clean_json_text(text)
    if not cleaned:
        raise AIJSONError("the model returned an empty completion", provider)
    slice_ = _balanced_slice(cleaned) or cleaned
    decommaed = re.sub(r",\s*([}\]])", r"\1", slice_)
    candidates = [cleaned, slice_, decommaed, _close_truncated(decommaed)]

    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, str):  # double-encoded: "{\"a\": 1}"
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
        if isinstance(parsed, list):  # some models wrap the object in an array
            parsed = next((item for item in parsed if isinstance(item, dict)), None)
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    raise AIJSONError(
        f"could not parse JSON from the model: {last_error}; raw text begins "
        f"{cleaned[:240]!r}", provider)


# --- public API --------------------------------------------------------------

async def chat_json(system_prompt: str, user_prompt: str, *, temperature: float = 0.0,
                    max_tokens: int | None = None, model: str | None = None) -> dict:
    """One chat completion that must yield a JSON object. Raises AIError."""
    cfg = chat_config()
    if cfg is None:
        raise AIConfigError(
            "no AI provider configured: set GROQ_API_KEY or GEMINI_API_KEY "
            "(see .env.example) or leave extraction to the offline heuristic")
    body: dict = {
        "model": model or cfg.model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens or env.EXTRACTOR_MAX_TOKENS,
    }
    if env.get_bool("AI_USE_JSON_MODE", True):
        # Documented for Groq and Gemini's OpenAI layer; a provider that still
        # refuses it gets the field dropped and retried inside _post_json.
        body["response_format"] = {"type": "json_object"}

    last_error: AIError | None = None
    for json_attempt in range(1, 3):
        data = await _post_json(cfg.url, cfg.headers, body, provider=cfg.provider)
        usage = CHAT_USAGE.get()
        if usage is not None:
            reported = data.get("usage") or {}
            usage["requests"] = usage.get("requests", 0) + 1
            usage["input_tokens"] = usage.get("input_tokens", 0) + reported.get("prompt_tokens", 0)
            usage["output_tokens"] = usage.get("output_tokens", 0) + reported.get("completion_tokens", 0)
            usage["usage_reported"] = bool(reported) and usage.get("usage_reported", True)
            usage["provider"], usage["model"] = cfg.provider, body["model"]
        if env.DEBUG:
            log.debug("[ai] %s raw body: %s", cfg.provider, str(data)[:2000])
        try:
            text = completion_text(data, cfg.provider)
            return parse_json_object(text, cfg.provider)
        except AIJSONError as exc:
            # One retry, phrased as a correction, recovers a malformed object
            # more often than a resend of the same prompt does.
            log.warning("[ai] %s returned unparseable JSON (attempt %d): %s",
                        cfg.provider, json_attempt, exc)
            last_error = exc
            if json_attempt == 1:
                body["messages"].append({"role": "user", "content": (
                    "That was not valid JSON. Reply again with ONLY the JSON object: "
                    "no markdown fence, no commentary, no trailing commas."
                )})
                body.pop("response_format", None)  # it clearly did not help
    raise last_error or AIJSONError("no usable completion", cfg.provider)


async def chat_text(system_prompt: str, user_prompt: str, *,
                    temperature: float = 0.2) -> str:
    """Plain-text completion; used by the connectivity check, never by ingest."""
    cfg = chat_config()
    if cfg is None:
        raise AIConfigError("no AI provider configured")
    body = {"model": cfg.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": 64}
    data = await _post_json(cfg.url, cfg.headers, body, provider=cfg.provider)
    return completion_text(data, cfg.provider)

def _normalize(vector: list[float]) -> list[float]:
    """L2-normalise; Gemini's docs require this after dimension truncation."""
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _guard_dimensions(vector, cfg: EmbeddingConfig, position: int) -> list[float] | None:
    """Refuse a vector that would not fit the pgvector column.

    A width mismatch is otherwise a silent data bug: pgvector rejects the
    insert, or worse, a future cosine operator mis-scores half-empty vectors.
    """
    if not isinstance(vector, (list, tuple)) or not vector:
        log.error("[embed] %s returned no vector for item %d", cfg.label, position)
        return None
    if len(vector) != cfg.dims:
        log.error(
            "[embed] %s returned %d dimensions but the database column is %d: "
            "set EMBEDDING_MODEL/EMBEDDING_DIMS to a matching pair and re-embed "
            "existing rows (see docs/OPERATIONS.md). Skipping this vector.",
            cfg.label, len(vector), cfg.dims)
        return None
    result = [float(value) for value in vector]
    return _normalize(result) if cfg.normalize else result


async def embed_texts(texts: Sequence[str], *, task: str = "document") -> list[list[float] | None]:
    """Embed a batch, positionally. A failed item is None, never a raised error.

    Callers store None as "no vector" and search.py falls back to BM25/keyword
    recall, so an outage degrades result quality instead of losing the upload.
    """
    cfg = embedding_config()
    if cfg is None:
        return [None] * len(texts)

    results: list[list[float] | None] = [None] * len(texts)
    # Blank text is not worth a request: Gemini embeds it into a meaningless
    # vector and Groq's nomic endpoint answers 400 for an empty input.
    pending = [i for i, text in enumerate(texts) if text and text.strip()]
    if not pending:
        return results

    for start in range(0, len(pending), env.EMBEDDING_BATCH_SIZE):
        indices = pending[start:start + env.EMBEDDING_BATCH_SIZE]
        try:
            if cfg.style == "gemini_native":
                vectors = await _gemini_embed(cfg, [texts[i] for i in indices], task)
            else:
                vectors = await _openai_embed(cfg, [texts[i] for i in indices])
        except AIError as exc:
            log.error("[embed] %s batch of %d failed: %s", cfg.label, len(indices), exc)
            continue
        if len(vectors) != len(indices):
            log.error("[embed] %s returned %d vectors for %d inputs; keeping the "
                      "first %d", cfg.label, len(vectors), len(indices),
                      min(len(vectors), len(indices)))
        for offset, index in enumerate(indices[:len(vectors)]):
            results[index] = _guard_dimensions(vectors[offset], cfg, index)
    return results


async def _gemini_embed(cfg: EmbeddingConfig, texts: list[str], task: str) -> list:
    """batchEmbedContents: the native endpoint, so outputDimensionality works."""
    # FREE_TINKER_* aside, taskType is what separates a document vector from a
    # query vector; cosine between mismatched task types is meaningless.
    task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
    requests = []
    for text in texts:
        request: dict = {
            "model": f"models/{cfg.model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
        }
        request["outputDimensionality"] = cfg.dims
        requests.append(request)
    data = await _post_json(f"{cfg.url}:batchEmbedContents", cfg.headers,
                            {"requests": requests}, provider=cfg.provider)
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list):
        raise AIProtocolError(
            f"gemini embeddings response has no 'embeddings' list; keys: {sorted(data)[:8]}",
            cfg.provider)
    return [item.get("values") if isinstance(item, dict) else item for item in embeddings]


async def _openai_embed(cfg: EmbeddingConfig, texts: list[str]) -> list:
    """The /v1/embeddings dialect: Groq, OpenAI, and OpenAI-compatible servers."""
    body: dict = {"model": cfg.model, "input": texts}
    if cfg.provider == "openai" or cfg.provider == "openai_compatible":
        # text-embedding-3 supports MRL truncation; older/compatible servers
        # reject the field, and _post_json drops it and retries.
        body["dimensions"] = cfg.dims
    data = await _post_json(cfg.url, cfg.headers, body, provider=cfg.provider)
    items = data.get("data")
    if not isinstance(items, list):
        raise AIProtocolError(
            f"{cfg.provider} embeddings response has no 'data' list; "
            f"keys: {sorted(data)[:8]}", cfg.provider)
    # Provider controls ordering via 'index'; do not trust the array order.
    ordered: list = [None] * len(items)
    for position, item in enumerate(items):
        if isinstance(item, dict):
            index = item.get("index", position)
            vector = item.get("embedding", item.get("values"))
        else:
            index, vector = position, item
        if isinstance(index, int) and 0 <= index < len(ordered):
            ordered[index] = vector
        else:
            ordered[position] = vector
    return ordered


async def embed_text(text: str, *, task: str = "document") -> list[float] | None:
    """Embed one string. None means "no vector", and callers must cope."""
    if not text or not text.strip():
        return None
    vectors = await embed_texts([text], task=task)
    return vectors[0] if vectors else None







