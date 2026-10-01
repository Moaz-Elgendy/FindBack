"""Turns fetched page text into an ExtractedMemory via a chat model.

The provider call lives in app.services.ai so this module stays about the
domain: the prompt, mapping the model's JSON onto our schema, and the offline
heuristic that keeps ingest working when no provider answers.
"""
from __future__ import annotations

import logging

from app import env
from app.schemas import ExtractedMemory
from app.services import ai

log = logging.getLogger("findback.extractor")

SYSTEM_PROMPT = """You are FindBack's memory extractor. Given raw content scraped from a saved link, return a STRICT JSON object with:
{
  "summary": "25-word faithful summary, no hallucination",
  "key_points": ["3 concise bullet takeaways"],
  "category": "one of: recipe, tutorial, tool, product, video, article, other",
  "entities": {"ingredients": [], "tech": [], "people": [], "topics": [], "products": []},
  "intent": "one of: learn, cook, buy, watch, read, other",
  "tags": ["3 short lowercase tags"],
  "title_clean": "clean human title, max 12 words"
}
Rules: Be faithful to content. If field unknown, use [] or "other". No extra keys. JSON only."""

FALLBACK = ExtractedMemory(
    summary="Saved content awaiting AI processing.",
    key_points=["Open original to view content","AI summary unavailable yet","Try searching by what you remember"],
    category="other", entities={}, intent="other", tags=["saved","pending","memory"], title_clean="Saved Memory"
)

CATEGORIES = ("recipe", "tutorial", "tool", "product", "video", "article", "other")
INTENTS = ("learn", "cook", "buy", "watch", "read", "other")
# Providers count tokens, not characters, but a cap keeps a huge page from
# becoming an oversized bill and keeps prompts inside small model contexts.
MAX_CONTENT_CHARS = env.get_int("EXTRACTOR_MAX_CHARS", 8000)


async def extract_memory(raw_text: str, url_title: str = "") -> ExtractedMemory:
    """Never raises: any provider failure falls back to the offline heuristic.

    Ingest must not lose a saved link because a model timed out, so a failure
    is logged with its reason and the item still reaches status=ready - with
    heuristic fields the user can see and re-process later.
    """
    if not raw_text or not raw_text.strip():
        # Nothing to ask about; an empty prompt wastes a call, and Groq's
        # embeddings endpoint rejects empty input outright with a 400.
        return _heuristic(raw_text or "", url_title)
    try:
        user_content = f"URL title hint: {url_title}\n\nContent (truncated):\n{raw_text[:MAX_CONTENT_CHARS]}"
        data = await ai.chat_json(SYSTEM_PROMPT, user_content, temperature=0.1)
    except ai.AIConfigError as exc:
        log.info("[extractor] no provider configured, using heuristic: %s", exc)
        return _heuristic(raw_text, url_title)
    except ai.AIError as exc:
        log.warning("[extractor] %s: %s", type(exc).__name__, exc)
        return _heuristic(raw_text, url_title)
    except Exception as exc:  # a bug in this module must not fail the ingest task
        log.exception("[extractor] unexpected failure (%s): %s", type(exc).__name__, exc)
        return _heuristic(raw_text, url_title)

    try:
        return _from_model(data)
    except Exception:
        # Valid JSON carrying the wrong keys or shapes is a model problem, not a crash.
        log.warning("[extractor] could not map model output: %s", str(data)[:500])
        return _heuristic(raw_text, url_title)


def _str_list(value, limit: int) -> list[str]:
    """Coerce a model-provided value into a bounded list of plain strings.

    ExtractedMemory declares List[str] and Dict[str, List[str]], and models
    routinely answer with a bare string, a nested object, or numbers; pydantic
    would reject the whole object, losing an otherwise good extraction.
    """
    if value is None:
        return []
    if isinstance(value, str):  # one string where a list was expected
        value = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
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


def _from_model(data: dict) -> ExtractedMemory:
    """Map a model object onto ExtractedMemory, clamping to allowed values."""
    entities: dict[str, list[str]] = {}
    raw_entities = data.get("entities")
    if isinstance(raw_entities, dict):
        for key, values in raw_entities.items():
            names = _str_list(values, 25)
            if names:
                entities[str(key)] = names
    elif isinstance(raw_entities, list):  # flattened: ["Postgres", "AWS"]
        entities["topics"] = _str_list(raw_entities, 25)

    category = str(data.get("category", "other")).strip().lower()
    intent = str(data.get("intent", "other")).strip().lower()
    summary = str(data.get("summary", "")).strip()[:300]
    return ExtractedMemory(
        summary=summary or FALLBACK.summary,
        key_points=_str_list(data.get("key_points"), 3),
        category=category if category in CATEGORIES else "other",
        entities=entities,
        intent=intent if intent in INTENTS else "other",
        # Lowercasing lives here, not in the schema, so heuristic and model tags
        # stay comparable in search and in the UI.
        tags=[tag.lower() for tag in _str_list(data.get("tags"), 3)],
        title_clean=str(data.get("title_clean", "")).strip()[:120] or FALLBACK.title_clean,
    )



def _heuristic(text: str, hint: str) -> ExtractedMemory:
    t = (hint + " " + text).lower()
    category = "article"
    if any(k in t for k in ["recipe","ingredient","cook","chicken","mushroom"]): category="recipe"
    elif any(k in t for k in ["tutorial","how to","fix","aws","tool"]): category="tutorial"
    elif any(k in t for k in ["buy","price","$"]): category="product"
    title_clean = hint[:80] if hint else text[:80].split("\n")[0][:80] or "Saved Memory"
    summary = text[:200].split(".")[0][:180] + "." if text else "Saved for later."
    return ExtractedMemory(summary=summary, key_points=[summary.strip()[:100]], category=category,
        entities={"topics":[category]}, intent="learn" if category in ("tutorial","article") else "other",
        tags=[category,"saved","memory"], title_clean=title_clean)
