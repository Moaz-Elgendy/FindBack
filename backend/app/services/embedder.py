"""Embeddings for FindBack: text in, pgvector-shaped float lists out.

Thin wrapper over app.services.ai. This module owns the *shape* of the text we
embed (memory_string, chunk_text) and the provider's input limits; ai.py owns
transport, retries, and the differing JSON shapes.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from app import env
from app.services import ai

log = logging.getLogger("findback.embedder")

# Providers count tokens, not characters; these caps are a cheap guard. Gemini's
# embedding input limit is 2048 tokens (~8k chars of English) and Groq's nomic
# model truncates at 8192, so one number serves both without wasting a call.
MAX_EMBED_CHARS = env.get_int("EMBED_MAX_CHARS", 8000)


def memory_string(title_clean: str, summary: str, key_points: list, entities: dict) -> str:
    parts = []
    if title_clean: parts.append(title_clean)
    if summary: parts.append(summary)
    if key_points: parts.extend(key_points)
    for vals in (entities or {}).values():
        if isinstance(vals, list): parts.extend([str(v) for v in vals])
    text = " ".join(parts).strip()
    return text[:6000] if text else (summary or title_clean or "saved memory")

async def embed_text(text: str, *, task: str = "document") -> list[float] | None:
    """Embed one string, or None when no provider is configured or the call failed.

    Returning None instead of raising is the contract every caller depends on:
    ingest then stores a vector-less row and search.py drops to BM25/keyword
    recall, so an API outage degrades result quality instead of losing saves.
    """
    if not text or not text.strip():
        return None
    vectors = await ai.embed_texts([text[:MAX_EMBED_CHARS]], task=task)
    return vectors[0] if vectors else None


async def embed_many(texts: Sequence[str], *, task: str = "document") -> list[list[float] | None]:
    """Embed a list positionally, one HTTP request per provider-sized batch.

    Ingest uses this so an item's memory string and its chunks cost one call
    instead of N+1 sequential ones.
    """
    if not texts:
        return []
    trimmed = [text[:MAX_EMBED_CHARS] if text else "" for text in texts]
    return await ai.embed_texts(trimmed, task=task)


def embedding_model_name() -> str:
    """The model id to record on a row, so stale vectors can be found later."""
    cfg = ai.embedding_config()
    return cfg.model if cfg else ""

def chunk_text(text: str, size: int = 800, overlap: int = 160) -> list[str]:
    words = text.split()
    if len(words) <= size: return [text]
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i+size]))
        if i + size >= len(words): break
        i += size - overlap
    return chunks
