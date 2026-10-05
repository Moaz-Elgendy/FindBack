"""The explicit processing pipeline (Phase 8).

Stages run in a fixed order:

    FETCH -> NORMALIZE -> UNDERSTAND -> BRIEF -> CHUNK -> EMBED -> READY

Each stage is a separate function and each persists its output before moving
on, so a retry resumes at the stage that failed instead of repeating expensive
work. If EMBED fails, FETCH is not called again and the AI call behind
UNDERSTAND is not paid for twice.

Stage artifacts live on the item row, which is what makes resume possible:

    FETCH      -> raw_text, fetch_metadata, raw_s3_key, thumbnail_url
    NORMALIZE  -> normalized_text
    UNDERSTAND -> title_clean, entities, intent, tags, category
    BRIEF      -> summary, key_points
    CHUNK      -> chunk_texts
    EMBED      -> embedding, and the Chunk rows

Stage functions are looked up on the module at call time so a test can replace
one stage without touching the runner.
"""
from __future__ import annotations

import re

from app import env
from app.schemas import Brief
from app.services import ai, brief_v2, embedder, extractor, fetcher, media_understanding, storage
import logging
import time

log = logging.getLogger("findback.pipeline")
from app.services.extractor import memory_from_brief
from app.services.limits import AI_LIMIT, EMBEDDING_LIMIT, FETCH_LIMIT

STAGE_FETCH = "FETCH"
STAGE_NORMALIZE = "NORMALIZE"
STAGE_UNDERSTAND = "UNDERSTAND"
STAGE_BRIEF = "BRIEF"
STAGE_CHUNK = "CHUNK"
STAGE_EMBED = "EMBED"
STAGE_READY = "READY"

STAGE_ORDER = (STAGE_FETCH, STAGE_NORMALIZE, STAGE_UNDERSTAND, STAGE_BRIEF,
               STAGE_CHUNK, STAGE_EMBED)

MAX_RAW_CHARS = 12000
_WHITESPACE = re.compile(r"\s+")


def resume_index(last_stage: str | None) -> int:
    """Index of the first stage to run, given what already completed.

    A finished stage is skipped and its output read back from the item. An
    unknown or missing marker means start from the beginning.
    """
    if last_stage and last_stage in STAGE_ORDER:
        return STAGE_ORDER.index(last_stage) + 1
    return 0


async def stage_fetch(item, raw_preview: str = "") -> None:
    """Get the page text. Phase 7: bounded by the fetch concurrency budget."""
    with FETCH_LIMIT:
        try:
            fetched = await fetcher.fetch_content(item.url, raw_preview or "")
        except Exception:
            if not fetcher.video_source(item.url or ""): raise
            fetched = {"text": raw_preview or getattr(item, "raw_preview", None) or "", "title": item.title or "",
                       "input_provenance": "caption" if raw_preview else "none",
                       "fetch_errors": ["metadata fetch failed"]}
    if "input_provenance" in fetched:
        if fetcher.video_source(item.url or ""):
            bundle, media_meta = await media_understanding.acquire(
                item.url, fetched, getattr(item, "user_id", None),
                cache_lookup=media_understanding.cached_evidence)
        else:
            bundle = media_understanding.initial_bundle(item.url, fetched)
            media_meta = {}
        bundle.fetch_errors.extend(fetched.get("fetch_errors", []))
        item.evidence_bundle = bundle.model_dump()
        old_meta = getattr(item, "processing_metadata", None) or {}
        item.processing_metadata = dict(old_meta, **media_meta)
        item.processing_metadata["media_attempts"] = old_meta.get("media_attempts", 0) + 1
        if bundle.title: fetched["title"] = bundle.title
        if bundle.transcript:
            fetched["text"] = "\n".join(f"[{brief_v2.timestamp(s.start)}] {s.text}" for s in bundle.transcript)
    item.raw_text = fetched.get("text", "")[:MAX_RAW_CHARS]
    item.fetch_metadata = {k: v for k, v in fetched.items() if k != "text"}
    item.raw_s3_key = storage.store_raw_snapshot(str(item.id), fetched)
    thumbnail = fetched.get("thumbnail")
    if thumbnail and not item.thumbnail_url:
        item.thumbnail_url = thumbnail
    if fetched.get("title") and (not item.title or fetcher.url_only(item.title)):
        item.title = fetched["title"]
    if fetched.get("source_type"):
        item.source_type = fetcher.video_source(item.url) or fetched["source_type"]


async def stage_normalize(item) -> None:
    """Collapse whitespace. No network and no model: cheap and repeatable."""
    item.normalized_text = _WHITESPACE.sub(" ", (item.raw_text or "")).strip()


async def stage_understand(item) -> None:
    """The AI read of the content: the Brief."""
    metadata = item.fetch_metadata or {}
    title = item.title or ""
    if fetcher.url_only(title) and metadata.get("title"):
        title = metadata["title"]
    evidence = getattr(item, "evidence_bundle", None) or {}
    if evidence:
        evidence = dict(evidence, title=title or evidence.get("title", ""))
        began = time.monotonic()
        processing = dict(getattr(item, "processing_metadata", None) or {})
        usage = {}
        usage_token = ai.CHAT_USAGE.set(usage)
        try:
            with AI_LIMIT:
                result = await brief_v2.extract(evidence)
            item.brief_v2 = result.model_dump()
            brief = brief_v2.legacy(result)
            processing["brief_fallback"] = False
        except Exception as exc:
            log.warning("[brief] item=%s validation/provider failure=%s", getattr(item, "id", ""), type(exc).__name__)
            item.brief_v2, brief = brief_v2.offline(evidence)
            processing["brief_fallback"] = True
            processing["tag_shortfall"] = len(item.brief_v2["tags"]) < 15
        finally:
            ai.CHAT_USAGE.reset(usage_token)
        processing["llm_usage"] = usage
        pricing = env.get("BRIEF_INPUT_COST_PER_MILLION") and env.get("BRIEF_OUTPUT_COST_PER_MILLION")
        processing["llm_cost"] = ((usage.get("input_tokens", 0) * env.get_float("BRIEF_INPUT_COST_PER_MILLION", 0)
                                  + usage.get("output_tokens", 0) * env.get_float("BRIEF_OUTPUT_COST_PER_MILLION", 0))
                                  / 1_000_000) if pricing and usage.get("usage_reported") else None
        processing.update(prompt_version=brief_v2.PROMPT_VERSION, brief_seconds=time.monotonic() - began)
        config = ai.chat_config()
        processing["brief_model"] = usage.get("model") or (config.model if config else None)
        processing["brief_provider"] = usage.get("provider") or (config.provider if config else None)
        item.processing_metadata = processing
        item.fetch_metadata = dict(metadata, brief=brief.model_dump())
        item.title_clean = item.brief_v2["title"]
        item.entities = item.brief_v2["entities"]
        item.tags = item.brief_v2["tags"]
        item.intent = "other"
        return
    provenance = metadata.get("input_provenance")
    with AI_LIMIT:
        # Called through the module so a test can replace the extraction call.
        brief = await extractor.extract_brief(
            item.normalized_text or item.raw_text or "",
            title, url=item.url or "",
            **({"input_provenance": "caption"} if provenance == "caption" else {}))
    # The whole brief goes into fetch_metadata. It is JSONB and free-form, so a
    # new content type or a new brief field needs no DB migration (Phase 9).
    item.fetch_metadata = dict(item.fetch_metadata or {},
                               brief=brief.model_dump())
    memory = memory_from_brief(brief)
    item.title_clean = memory.title_clean or item.title
    item.entities = memory.entities
    item.intent = memory.intent
    item.tags = memory.tags


async def stage_brief(item) -> None:
    """Store the brief on the columns the app already reads.

    The BRIEF format is unchanged from Phase 8: `highlights` becomes
    `key_points` verbatim, so nothing is forced to three bullets.
    """
    stored = (item.fetch_metadata or {}).get("brief") or {}
    brief = Brief(**stored) if stored else Brief()
    memory = memory_from_brief(brief)
    modern = getattr(item, "brief_v2", None) or {}
    item.summary = modern.get("instant_brief") or memory.summary
    item.key_points = [p["point"] for p in modern["key_points"]] if modern else memory.key_points
    item.category = "video" if fetcher.video_source(item.url or "") else memory.category
    # Phase 12: the lexical document, so a word held only in structured_data is
    # still findable. Built here because BRIEF is where the brief becomes final.
    item.search_text = search_document(
        item.title_clean or item.title or "", brief.overview,
        brief.highlights, brief.entities, brief.topics,
        brief.structured_data) + " " + (item.url or "")
    if modern:
        item.search_text = " ".join([modern["title"], modern["instant_brief"],
                                     *item.key_points, *modern["tags"], *modern["search_phrases"],
                                     *[str(v) for values in modern["entities"].values() for v in values],
                                     item.url or ""])


def search_document(title_clean: str, overview: str, highlights: list,
                    entities: list, topics: list, structured: dict) -> str:
    """Everything worth matching words against, as one document (Phase 12).

    Without this, a word the user remembers from a recipe's ingredients or a
    product's price is invisible: it is in `structured_data`, not in the title
    or the one-line summary, so no lexical search can reach it.

    Nested values are flattened by key path, so a brand inside a product spec
    is still searchable as a word.
    """
    parts = [title_clean or "", overview or ""]
    parts.extend(str(h) for h in (highlights or []))
    parts.extend(str(e) for e in (entities or []))
    parts.extend(str(t) for t in (topics or []))

    def walk(prefix, value):
        if isinstance(value, dict):
            for key, sub in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), sub)
        elif isinstance(value, (list, tuple)):
            for sub in value:
                walk(prefix, sub)
        elif value is not None:
            parts.append(str(prefix))
            parts.append(str(value))

    walk("", structured or {})
    return " ".join(p for p in parts if p).strip()


async def stage_chunk(item) -> None:
    """Split the normalized text into the pieces EMBED will vectorise.

    Timestamps are read from the transcript where it has them, so EMBED can
    write a chunk that knows when it starts.
    """
    transcript = (getattr(item, "evidence_bundle", None) or {}).get("transcript", [])
    text = "\n".join(f"[{brief_v2.timestamp(s['start'])}] {s['text']}" for s in transcript) if transcript else item.normalized_text or ""
    chunks = embedder.chunk_text_with_timestamps(text)
    item.chunk_texts = [c["chunk_text"] for c in chunks]
    item.chunk_timestamps = [{"timestamp": c["start_timestamp"],
                              "seconds": c["start_seconds"]} for c in chunks]


async def stage_embed(item, db) -> None:
    """Vectorise the memory string and every chunk, then write the Chunk rows."""
    from app.models import Chunk

    mem_str = embedder.memory_string(
        item.title_clean or "", item.summary or "", item.key_points or [],
        item.entities or {}, tags=item.tags or [],
        search_phrases=(getattr(item, "brief_v2", None) or {}).get("search_phrases", []))
    chunks = list(item.chunk_texts or [])
    stamps = list(item.chunk_timestamps or [])
    with EMBEDDING_LIMIT:
        vectors = await embedder.embed_many([mem_str] + chunks)
    memory_vector = vectors[0] if vectors else None
    chunk_vectors = vectors[1:] if vectors else []
    if memory_vector:
        item.embedding = memory_vector
        item.embedding_model = embedder.embedding_model_name()
    if any(vectors):
        item.embedding_model = embedder.embedding_model_name()
        # Replace rather than append: re-running EMBED must not duplicate rows.
        db.query(Chunk).filter(Chunk.item_id == item.id).delete()
        for index, (content, vector) in enumerate(zip(chunks, chunk_vectors)):
            if vector:
                stamp = stamps[index] if index < len(stamps) else None
                db.add(Chunk(item_id=item.id, chunk_idx=index,
                             chunk_text=content, embedding=vector,
                             start_timestamp=(stamp or {}).get("timestamp"),
                             start_seconds=(stamp or {}).get("seconds")))


# Resolved at call time so a test can replace a single stage in isolation.
_STAGES = {
    STAGE_FETCH: stage_fetch,
    STAGE_NORMALIZE: stage_normalize,
    STAGE_UNDERSTAND: stage_understand,
    STAGE_BRIEF: stage_brief,
    STAGE_CHUNK: stage_chunk,
    STAGE_EMBED: stage_embed,
}
