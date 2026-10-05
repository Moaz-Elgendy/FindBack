"""Turns fetched page text into a Brief (and the ExtractedMemory derived from it).

The provider call lives in app.services.ai so this module stays about the
domain: the prompt, validating the model's JSON against the Brief schema, and
the offline heuristic that keeps ingest working when no provider answers.

The prompt states the shape of the Brief and nothing about content types.
Prompts per content type are Phase 10; this phase only fixes the schema.
"""
from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from app import env
from app.categories import CATEGORIES
from app.schemas import Brief, ExtractedMemory
from app.services import ai, privacy, profiles, fetcher
from app.services.ai_gateway import get_gateway

log = logging.getLogger("findback.extractor")

# The brief's fields are exactly these. Phase 10 decides how to prompt for each
# content type; the shape itself is fixed here.
BRIEF_SYSTEM_PROMPT = """You are FindBack's memory extractor. Given raw content saved by a user, return a STRICT JSON object with exactly these keys:
{
  "title": "clean human title, max 12 words",
  "overview": "what this content is and why it is useful, max 40 words, no hallucination",
  "highlights": ["the concrete takeaways, one per entry"],
  "structured_data": {},
  "entities": ["named things: people, products, tools, ingredients"],
  "topics": ["short subject tags"],
  "intent": ["what the user would do with this: learn, cook, buy, watch, read"],
  "actions": ["concrete steps or calls to action, in order"],
  "timestamps": ["mm:ss markers that matter, only if the content is timed"]
}
Rules: Be faithful to the content. `highlights` must have as many entries as the content genuinely has; never pad it and never cut it to three. Put whatever does not fit the fields above into `structured_data` as free-form JSON. If a field has nothing, use "" or []. No keys other than these nine. JSON only."""

FALLBACK = ExtractedMemory(
    summary="Saved content awaiting AI processing.",
    key_points=["Open original to view content", "AI summary unavailable yet",
                "Try searching by what you remember"],
    category="other", entities={}, intent="other",
    tags=["saved", "pending", "memory"], title_clean="Saved Memory",
)

INTENTS = ("learn", "cook", "buy", "watch", "read", "other")
# Providers count tokens, not characters, but a cap keeps a huge page from
# becoming an oversized bill and keeps prompts inside small model contexts.
MAX_CONTENT_CHARS = env.get_int("EXTRACTOR_MAX_CHARS", 8000)

# Enough for a long "everything it covers" list without inviting a wall of text.
MAX_HIGHLIGHTS = 25


async def extract_brief(raw_text: str, url_title: str = "",
                       url: str = "", input_provenance: str = "page") -> Brief:
    """Pick a profile, ask the model for a brief in that shape, validate it.

    Never raises: any provider failure falls back to the offline heuristic.
    Ingest must not lose a saved link because a model timed out, so a failure
    is logged with its reason and the item still reaches status=ready - with
    heuristic fields the user can see and re-process later.
    """
    return await extract_brief_through(get_gateway(), raw_text, url_title, url, input_provenance)


async def extract_brief_through(gateway, raw_text: str, url_title: str = "",
                                url: str = "", input_provenance: str = "page") -> Brief:
    """The extraction policy, written against the gateway rather than a provider.

    Everything provider-specific was decided before this function was entered:
    which endpoint, which key, which model. Here we only decide what to ask for.
    """
    profile = profiles.classify(url=url, text=raw_text or "", title=url_title)
    if not raw_text or not raw_text.strip() or fetcher.url_only(raw_text):
        # Nothing to ask about; an empty prompt wastes a call, and some
        # embeddings endpoints reject empty input outright with a 400.
        return _heuristic_brief(raw_text or "", url_title, profile)
    try:
        user_content = f"URL title hint: {url_title}\n\nContent (truncated):\n{raw_text[:MAX_CONTENT_CHARS]}"
        prompt = system_prompt(profile)
        if input_provenance == "caption":
            prompt += "\nExtract every named item and claim from the caption and title. Do not invent spoken content or timestamps. Never describe extraction failures."
        data = await gateway.generate_json(prompt, user_content,
                                           temperature=0.1)
    except ai.AIConfigError as exc:
        log.info("[extractor] no provider configured, using heuristic: %s", exc)
        return _heuristic_brief(raw_text, url_title, profile)
    except ai.AIError as exc:
        log.warning("[extractor] %s: %s", type(exc).__name__, exc)
        return _heuristic_brief(raw_text, url_title, profile)
    except Exception as exc:  # a bug in this module must not fail the ingest task
        log.exception("[extractor] unexpected failure (%s): %s", type(exc).__name__, exc)
        return _heuristic_brief(raw_text, url_title, profile)

    try:
        return brief_from_model(data, profile=profile)
    except Exception:
        # Valid JSON that is not a valid Brief is a model problem, not a crash.
        # Note the extra-field case specifically: the schema forbids unknown keys,
        # so a brief the model invented must not be accepted.
        #
        # The model's answer is derived from the user's private content, so it
        # is described, never printed (Phase 14).
        log.warning("[extractor] model output is not a valid Brief (%s)",
                    privacy.describe(str(data), "answer"))
        return _heuristic_brief(raw_text, url_title, profile)


def system_prompt(profile) -> str:
    """The shared brief prompt plus the chosen profile's instructions.

    One model call, not two: the profile is decided locally and folded into the
    prompt. `BRIEF_SYSTEM_PROMPT` is kept as the base so the Phase 9 prompt
    contract still holds.
    """
    return f"{BRIEF_SYSTEM_PROMPT}\n{profile_for_prompt(profile)}"


async def extract_memory(raw_text: str, url_title: str = "",
                        url: str = "") -> ExtractedMemory:
    """The brief, mapped onto the columns the rest of the app already stores.

    summary/key_points/tags/entities keep their existing shape so search,
    embedding and the app screens keep working unchanged. What changes is where
    the values come from: a list of five skills is five key_points, not three.
    """
    brief = await extract_brief(raw_text, url_title, url=url)
    return memory_from_brief(brief)


def brief_from_model(data, profile=None) -> Brief:
    """Validate a model object against the Brief schema.

    Coercion happens before validation (models answer with bare strings and
    nulls), then the schema itself decides: an unknown top-level key raises.

    When a profile is given, its keys are also required inside
    `structured_data`. A brief that ignored its profile still validates against
    the Phase 9 schema, so the profile check has to be explicit.
    """
    if not isinstance(data, dict):
        # Surface this as a schema failure like any other invalid brief, so the
        # caller has one exception type to handle.
        raise ValidationError.from_exception_data(
            "Brief", [{"type": "model_type", "loc": (), "input": data,
                       "ctx": {"class_name": "Brief"}}])
    coerced = _coerce(data)
    if profile is not None:
        coerced["structured_data"] = _apply_profile(coerced, profile)
    return Brief(**coerced)


def _apply_profile(coerced: dict, profile) -> dict:
    """Fit the model's structured_data to the profile, keeping unknown keys.

    Profile keys are filled in even if the model omitted them, so the shape is
    the same for every brief of that profile. Keys the profile does not define
    are kept: `structured_data` is free-form (Phase 9), and a content type
    discovered later still has somewhere to go without a migration.
    """
    structured = dict(coerced.get("structured_data") or {})
    structured["content_type"] = profile.name
    for key in profile.keys:
        value = structured.get(key)
        if value is None:
            structured[key] = profile.defaults.get(key, [])
        elif isinstance(value, list):
            structured[key] = _str_list(value, 50)
        elif isinstance(value, (int, float, bool)):
            structured[key] = str(value)
        elif not isinstance(value, str):
            # A nested object where the profile wants text: keep the JSON rather
            # than losing it, since structured_data is free-form.
            structured[key] = value
        else:
            structured[key] = value.strip()
    return structured


def profile_for_prompt(profile) -> str:
    """The profile's instruction block, appended to the shared brief prompt."""
    return f"{profile.instructions}\nThe structured_data.content_type is " \
           f"\"{profile.name}\"."


def _str_list(value, limit: int) -> list[str]:
    """Coerce a model-provided value into a bounded list of plain strings.

    Models routinely answer with a bare string, a nested object, or numbers.
    Coercion happens here, before the Brief schema validates, so one odd entry
    does not cost the whole extraction.
    """
    if value is None:
        return []
    if isinstance(value, str):  # one string where a list was expected
        value = [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value[:limit]:
        if isinstance(item, dict):  # {"point": "..."} shaped bullets
            item = next(iter(item.values()), "")
        text = str(item).strip()
        if text:
            out.append(text)
    return out


# Field -> bound. Only the free-form `structured_data` is unbounded.
LIST_LIMITS = {
    "highlights": MAX_HIGHLIGHTS,
    "entities": 25,
    "topics": 15,
    "intent": 10,
    "actions": 25,
    "timestamps": 40,
}


def _coerce(data: dict) -> dict:
    """Normalise the shapes models actually emit, without inventing fields.

    Only the nine known keys are carried through. An unknown key is left in
    place on purpose so Brief's `extra="forbid"` rejects the whole brief rather
    than silently swallowing a field the model invented.
    """
    out: dict = {}
    for key in ("title", "overview"):
        value = data.get(key)
        out[key] = "" if value is None else str(value).strip()[:300]
    for key, limit in LIST_LIMITS.items():
        out[key] = _str_list(data.get(key), limit)
    # structured_data is free-form on purpose: a new content type adds keys
    # here and needs no migration.
    raw = data.get("structured_data")
    out["structured_data"] = raw if isinstance(raw, dict) else {}
    for key in data:
        if key not in LIST_LIMITS and key not in ("title", "overview",
                                                  "structured_data"):
            out[key] = data[key]
    return out


def memory_from_brief(brief: Brief) -> ExtractedMemory:
    """Map a Brief onto the columns the rest of the app already stores.

    `key_points` is `highlights` verbatim. That is the point of Phase 9: a
    five-item list stays five items instead of being cut to three.
    """
    category = _category_for(brief)
    structured = brief.structured_data or {}
    entities: dict[str, list[str]] = {}
    if brief.entities:
        entities["topics"] = list(brief.entities)
    if brief.actions:
        entities["actions"] = list(brief.actions)
    for key, values in structured.items():
        if isinstance(values, list) and values:
            entities[str(key)] = _str_list(values, 25)
    intent = next((i for i in brief.intent if i.lower() in INTENTS), "other")
    return ExtractedMemory(
        summary=(brief.overview or FALLBACK.summary)[:300],
        key_points=list(brief.highlights),
        category=category,
        entities=entities,
        intent=intent,
        tags=[t.lower() for t in brief.topics][:15] or [category],
        title_clean=(brief.title or FALLBACK.title_clean)[:120],
    )


def _category_for(brief: Brief) -> str:
    """The stored category, read off the profile the brief was extracted with."""
    structured = brief.structured_data or {}
    # An explicit category always wins: the offline heuristic writes one when it
    # recognises the text better than the profile name does.
    declared = str(structured.get("category", "")).strip().lower()
    if declared in CATEGORIES:
        return declared
    content_type = str(structured.get("content_type", ""))
    # The profile name and the stored category are deliberately not the same
    # thing: a `list` profile is how the content was read, and a video is what
    # it is. Where the two disagree the category still falls back to a guess.
    if content_type in ("recipe", "product", "tutorial"):
        return content_type
    hint = f"{content_type} {' '.join(brief.topics)}".lower()
    # A topic that already names a category is the strongest signal there is.
    for word in hint.split():
        if word in CATEGORIES:
            return word
    if content_type == "list" or any(k in hint for k in ("video", "watch", "talk")):
        return "video"
    if any(k in hint for k in ("tool", "software", "app", "library")):
        return "tool"
    return "article"


def _category_from_text(text: str, hint: str) -> str:
    """Offline guess at the content type, from the words actually present."""
    t = f"{hint} {text}".lower()
    if any(k in t for k in ("recipe", "ingredient", "cook", "preheat", "chicken",
                            "mushroom", "risotto")):
        return "recipe"
    if any(k in t for k in ("buy", "price", "review", "$")):
        return "product"
    if any(k in t for k in ("tutorial", "how to", "fix", "aws", "tool", "setup")):
        return "tutorial"
    return "article"


def _heuristic_brief(text: str, hint: str, profile=None) -> Brief:
    """The offline brief when no provider answers. Keeps ingest working."""
    if not text.strip() or fetcher.url_only(text):
        return Brief(title=hint or "Saved link", overview="This link could not be read. Open the original to view it.")
    overview = re.split(r"(?<=[.!?])\s+", text[:200], maxsplit=1)[0][:180]
    # Without a model there is no profile signal beyond the words, so the text
    # guess stands in. The profile's shape is still honoured.
    if profile is None:
        profile = profiles.classify(text=text, title=hint)
    content_type = profile.name if profile.name in (
        "recipe", "product", "tutorial") else _category_from_text(text, hint)
    brief = Brief(
        title=(hint[:80] if hint else text[:80].split("\n")[0][:80]) or "Saved Memory",
        overview=overview,
        highlights=[overview.strip()[:100]],
        topics=[content_type],
        intent=["learn"] if content_type in ("tutorial", "general", "article")
        else ["other"],
        structured_data={},
    )
    # The category is decided before the profile shape is applied: applying it
    # overwrites content_type, and the text guess is the better signal here.
    category = _category_for(brief)
    if profile is not None:
        brief.structured_data = _apply_profile(
            {"structured_data": {}}, profile)
    brief.structured_data["category"] = category
    return brief
