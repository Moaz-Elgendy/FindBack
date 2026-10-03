"""Embeddings for FindBack: text in, pgvector-shaped float lists out.

Thin wrapper over app.services.ai. This module owns the *shape* of the text we
embed (memory_string, chunk_text) and the provider's input limits; ai.py owns
transport, retries, and the differing JSON shapes.

Chunking also carries the timeline a video has (Phase 11). A chunk is a piece of
the content with its own embedding, so a query can match a *moment* rather than
the title, and each timed chunk remembers where it starts -- which is what lets
a search answer "the creator explains X at 14:02".

Transcript text usually carries its own markers (`[14:02]`, `(14:02)`, or a
bare `14:02`). Those are read, never invented, so an article gets no timestamp
rather than a fake one.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app import env

from app.services.ai_gateway import get_gateway

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
    vectors = await get_gateway().embed([text[:MAX_EMBED_CHARS]], task=task)
    return vectors[0] if vectors else None


async def embed_many(texts: Sequence[str], *, task: str = "document") -> list[list[float] | None]:
    """Embed a list positionally, one HTTP request per provider-sized batch.

    Ingest uses this so an item's memory string and its chunks cost one call
    instead of N+1 sequential ones.
    """
    if not texts:
        return []
    trimmed = [text[:MAX_EMBED_CHARS] if text else "" for text in texts]
    return await get_gateway().embed(trimmed, task=task)


def embedding_model_name() -> str:
    """The model id to record on a row, so stale vectors can be found later."""
    return get_gateway().model_name()

def parse_timestamp(value: str) -> int | None:
    """Seconds for an mm:ss or h:mm:ss marker, or None if it is not one."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", value.strip())
    if not match:
        return None
    if match.group(3) is not None:  # h:mm:ss
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    return int(match.group(1)) * 60 + int(match.group(2))


def chunk_text(text: str, size: int = 800, overlap: int = 160) -> list[str]:
    words = text.split()
    if len(words) <= size: return [text]
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i+size]))
        if i + size >= len(words): break
        i += size - overlap
    return chunks


# A marker anywhere in the text: its value, and where it starts. The brackets
# and the space before it are not consumed -- stripping them would leave a stray
# "]" at the front of the stored chunk text.
_MARKER = re.compile(
    r"(?:(?<=^)|(?<=[\s\[\(\-]))(\d{1,2}:\d{2}(?::\d{2})?)(?=$|[\s\]\)\.\,\:])")


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split timed content into sections, each keeping the time it starts at.

    A fixed-size split throws the timeline away: the chunk that begins at
    14:02's *third* sentence carries no marker of its own, so it would be filed
    as untimed content even though it plainly belongs to 14:02. Splitting at the
    markers first, then chunking inside each section, keeps the time attached to
    every chunk of that section.

    Text with no markers yields one untimed section, which chunks exactly as
    before.
    """
    sections: list[tuple[str | None, str]] = []
    position = 0
    current_stamp: str | None = None

    def body(start: int, end: int) -> str:
        # The marker sits between its brackets, so the text before it ends with
        # "[" and the text after it starts with "]". Trimmed here so neither half
        # keeps a stray bracket.
        return text[start:end].strip(" \t\n[]()")

    for match in _MARKER.finditer(text):
        piece = body(position, match.start())
        if piece:
            sections.append((current_stamp, piece))
        current_stamp = match.group(1)
        position = match.end()
    tail = body(position, len(text))
    if tail:
        sections.append((current_stamp, tail))
    return sections


def chunk_text_with_timestamps(text: str, size: int = 800,
                               overlap: int = 160) -> list[dict]:
    """Chunks that each remember which moment they belong to.

    Untimed content gets None rather than an invented time: an article's
    paragraphs have no minute 14, and pretending otherwise is a worse answer
    than saying nothing.
    """
    out: list[dict] = []
    for stamp, section in split_sections(text):
        for body in chunk_text(section, size, overlap):
            out.append({"chunk_idx": len(out),
                        "chunk_text": body.strip(),
                        "start_timestamp": stamp,
                        "start_seconds": parse_timestamp(stamp) if stamp else None})
    return out
