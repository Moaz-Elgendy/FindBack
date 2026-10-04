"""Hybrid memory search (Phase 12).

Retrieval only. Nothing here recommends, suggests, or invents content the user
never saved.

Two retrieval paths, merged and ranked:

    lexical  PostgreSQL full text over items.search_text and chunks.tsv
    vector   cosine distance over items.embedding and chunks.embedding

`search_text` (Phase 12) is the item's whole searchable document -- title,
overview, highlights, entities, topics, and whatever `structured_data` holds --
so a word the user remembers from a recipe's ingredients is findable even
though it appears in no title and no summary.

Every result carries a "matched because" reason built from terms that actually
matched. `Matched: tutorial` is not a reason: a category is not a match.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import embedder

log = logging.getLogger("findback.search")

RRF_K = int(os.getenv("RRF_K", "60"))
W_VEC = float(os.getenv("VECTOR_WEIGHT", "0.7"))
W_BM25 = float(os.getenv("BM25_WEIGHT", "0.3"))
# Phase 12: chunk evidence counts separately from item evidence. A chunk hit is
# a specific moment; an item hit is the whole memory.
W_CHUNK_VEC = float(os.getenv("CHUNK_VECTOR_WEIGHT", "0.5"))
W_CHUNK_LEX = float(os.getenv("CHUNK_LEXICAL_WEIGHT", "0.35"))
# Phase 12 + 13: the user's own note and intent. Above a body-word match,
# because a note is deliberate; below the memory's own vector, because a
# note says how the user filed the thing rather than what the content is.
W_NOTE_LEX = float(os.getenv("NOTE_LEXICAL_WEIGHT", "0.45"))
# How much a recent save can lift a result. This must stay far below the
# smallest possible RRF contribution (a single last-place hit in the smallest
# list is W_BM25/(RRF_K+limit) ~ 0.002), because recency is a tie-breaker: it
# may order two memories that matched equally, never one that matched nothing.
W_RECENCY = float(os.getenv("RECENCY_WEIGHT", "0.001"))

# Words that carry no retrieval signal: grammar, and how people speak about
# saving something. Deliberately short. Content words are NOT stopwords here --
# "the AI presentation tool" is a search for three real words, and treating
# "presentation" or "tool" as noise would throw away the user's own vocabulary.
STOPWORDS = frozenset("""
a an the this that these those my our your their its is are was were be been am
i me we you they he she it to of in on at for with about from by as and or but
if then than so such into over under again very just do does did doing
have has had having save saved saving want wanted need needed look looking
""".split())

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_.+]*")

# Column order shared by every candidate query below. `content_id` sits before
# the score so the score stays the LAST element: the keyword fallback below reads
# it with `row[-1]`, and moving it would silently return a content id instead.
_CONTENT_IDX = 8

# The item columns every candidate query selects, in a fixed order.
_ITEM_COLUMNS = ("i.id, i.title_clean, i.summary, i.tags, i.category, "
                 "i.source_domain, i.thumbnail_url, i.created_at, i.content_id")


def query_terms(query: str) -> list[str]:
    """The words of a query that carry retrieval signal, in query order."""
    seen, out = set(), []
    for word in _WORD.findall(query or ""):
        lowered = word.lower().strip("-._")
        if not lowered or lowered in STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        out.append(lowered)
    return out


def matched_terms(terms: list[str], *texts: str | None) -> list[str]:
    """Which of the query's words actually occur in these texts.

    This is what makes the reason honest: a term is only claimed as a match
    because the memory really contains it. Matching is on word starts so
    "deploy" counts for "deployment".
    """
    haystack = " ".join(t for t in texts if t).lower()
    hits = []
    for term in terms:
        if re.search(r"(?<![a-z0-9])" + re.escape(term), haystack):
            hits.append(term)
    return hits


def evidence_reason(terms: list[str], row, note: str | None = None,
                    chunk: tuple | None = None, document: str | None = None,
                    intent: str | None = None) -> str:
    """Say what matched, in the user's own words.

    "AWS + deployment + recovery" is a reason. "Matched: tutorial" is not: a
    category was never matched on, it only describes the memory.
    """
    # Everything that was indexed for this memory: the stored document, and the
    # user's own note about it.
    hits = matched_terms(terms, document, row[1], row[2],
                         " ".join(row[3] or []))
    if hits:
        return " + ".join(hits)

    # Nothing matched in the title, brief or topics: then it matched the body.
    chunk_text = chunk[10] if chunk else None
    body_hits = matched_terms(terms, chunk_text)
    if body_hits:
        stamp = chunk[12] if chunk else None
        where = f"at {stamp}" if stamp else "in the content"
        return f"{' + '.join(body_hits)} ({where})"

    if note and matched_terms(terms, note):
        return f"{' + '.join(matched_terms(terms, note))} (your note)"

    # The intent is the user's own label too, so it is evidence -- but it is not
    # the note, and saying so would be a wrong reason.
    if intent and matched_terms(terms, intent):
        return f"{' + '.join(matched_terms(terms, intent))} (your intent)"

    # No lexical evidence at all, so the reason is the semantic one: be honest
    # that it matched on meaning rather than pretend a word matched.
    return "similar meaning"


def recency_boost(created_at, now: datetime | None = None) -> float:
    """0.0 for an old save, up to 1.0 for one made just now.

    Half-life of ~30 days, so "recent" means weeks rather than minutes.
    """
    if created_at is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / 30.0)


def _cat_filter(category: str | None, alias: str = "") -> str:
    return f"AND {alias}category = :cat" if category else ""


def _with_notes(db: Session, user_id, ids: list[str]) -> dict[str, dict]:
    """The user's own private note and intent for these memories (Phase 13).

    A note is how the user remembers a thing: "the one I saved to try on the
    laptop". Both it and the intent are searchable, and neither is ever shared.
    Read from `user_memories` under the requesting user's id and nowhere else.
    """
    if not ids:
        return {}
    rows = db.execute(text("""
        SELECT um.content_id::text, um.user_note, um.user_intent
        FROM user_memories um
        WHERE um.user_id = :uid
          AND (um.user_note IS NOT NULL OR um.user_intent IS NOT NULL)
          AND um.content_id::text = ANY(:ids)
    """), {"uid": str(user_id), "ids": ids}).fetchall()
    return {row[0]: {"note": row[1], "intent": row[2]} for row in rows}


def _documents(db: Session, user_id, item_ids: list[str]) -> dict[str, str]:
    """The stored searchable document per item."""
    if not item_ids:
        return {}
    rows = db.execute(text("""
        SELECT id::text, coalesce(search_text, '')
        FROM items
        WHERE user_id = :uid AND id::text = ANY(:ids)
    """), {"uid": str(user_id), "ids": item_ids}).fetchall()
    return {row[0]: row[1] for row in rows}


def or_tsquery(terms: list[str]) -> str:
    """A tsquery that matches a document containing ANY of these terms.

    Built here rather than with plainto/websearch_to_tsquery because those
    default to AND, and a user who misremembers one word must still get the
    memory. Terms are emitted bare (not quoted): PostgreSQL has no string form
    for a quoted lexeme, and anything that is not a plain word is not a term
    worth searching for anyway.
    """
    safe = []
    for term in terms:
        cleaned = re.sub(r"[^a-z0-9]+", "", term.lower())
        if cleaned:
            safe.append(cleaned)
    # `|` is the tsquery OR operator. The word form ("OR") is rejected by
    # to_tsquery, which is why this is built explicitly rather than assembled
    # from a string of bare words.
    return " | ".join(safe)


def lexical_search(db: Session, user_id, query: str, terms: list[str],
                   category: str | None = None, limit: int = 50):
    """Full-text candidates from the item's whole searchable document.

    The terms are OR'd, not AND'd. `plainto_tsquery` ANDs every word, so "aws
    fixing deployment" would match only a memory containing all three, and a
    user who misremembers one word would get nothing at all. The OR is built
    explicitly rather than left to `websearch_to_tsquery`, which would depend
    on operator parsing.

    Both `search_text_tsv` and the original `tsv` are consulted: a memory saved
    before Phase 12 has no `search_text`, but it always has `tsv`, and it must
    not become unfindable because this phase added a better index.
    """
    if not terms:
        return []
    try:
        return db.execute(text(f"""
            SELECT {_ITEM_COLUMNS},
                   greatest(ts_rank(i.search_text_tsv, to_tsquery('english', :q)),
                            ts_rank(i.tsv, to_tsquery('english', :q))) AS rank
            FROM items i
            WHERE i.user_id = :uid AND i.status = 'ready'
              AND (i.search_text_tsv @@ to_tsquery('english', :q)
                   OR i.tsv @@ to_tsquery('english', :q))
              {_cat_filter(category, 'i.')}
            ORDER BY rank DESC
            LIMIT :lim
        """), {"uid": str(user_id), "q": or_tsquery(terms),
               "cat": category, "lim": limit}).fetchall()
    except Exception as e:
        log.warning("[search] lexical failed: %s", e)
        return []


def chunk_lexical_search(db: Session, user_id, query: str, terms: list[str],
                         limit: int = 50):
    """Full-text candidates from inside the content, with the matched chunk."""
    if not terms:
        return []
    try:
        return db.execute(text("""
            SELECT i.id, i.title_clean, i.summary, i.tags, i.category,
                   i.source_domain, i.thumbnail_url, i.created_at, i.content_id,
                   ts_rank(c.tsv, to_tsquery('english', :q)) AS rank,
                   c.chunk_text, c.chunk_idx, c.start_timestamp, c.start_seconds
            FROM chunks c
            JOIN items i ON i.id = c.item_id
            WHERE i.user_id = :uid AND i.status = 'ready'
              AND c.tsv @@ to_tsquery('english', :q)
            ORDER BY rank DESC
            LIMIT :lim
        """), {"uid": str(user_id), "q": or_tsquery(terms),
               "lim": limit}).fetchall()
    except Exception as e:
        log.warning("[search] chunk lexical failed: %s", e)
        return []


# The user's own words: the note they wrote and the intent they chose. Both
# live on their private memory row, so they are read as one document. Neither is
# ever copied onto the shared asset or into the embedding text.
_NOTE_DOCUMENT = ("coalesce(um.user_note, '') || ' ' || "
                  "coalesce(um.user_intent, '')")


def note_search(db: Session, user_id, terms: list[str], limit: int = 50):
    """Candidates from the user's own note and intent (Phase 12 + 13).

    A note is how the user remembers a thing -- "the one I saved to try on the
    laptop" -- so it is evidence about *their* memory, and a query that matches
    nothing but the note has to find the item. Before this, the note could only
    explain a match that some other candidate list had already produced, so the
    user's own words about why they saved something answered nothing.

    Two things this deliberately does not do:

    * read another user's row. `um.user_id` is the requester and the item join
      is restricted to the same user, so a note can only ever make its own
      author's item a candidate;
    * write the note anywhere shared. It stays on `user_memories`; it is not
      copied onto `content_assets` and not folded into the embedding text, so it
      cannot reach another user or another user's vector.
    """
    if not terms:
        return []
    try:
        return db.execute(text(f"""
            SELECT {_ITEM_COLUMNS},
                   ts_rank(to_tsvector('english', {_NOTE_DOCUMENT}),
                           to_tsquery('english', :q)) AS rank
            FROM user_memories um
            JOIN items i
              ON i.content_id = um.content_id AND i.user_id = um.user_id
            WHERE um.user_id = :uid
              AND to_tsvector('english', {_NOTE_DOCUMENT})
                  @@ to_tsquery('english', :q)
            ORDER BY rank DESC
            LIMIT :lim
        """), {"uid": str(user_id), "q": or_tsquery(terms),
               "lim": limit}).fetchall()
    except Exception as e:
        log.warning("[search] note search failed: %s", e)
        return []


def vector_search(db: Session, user_id, q_emb, category: str | None = None,
                  limit: int = 50):
    """Semantic candidates from the item's own memory vector."""
    if q_emb is None:
        return []
    vec_str = "[" + ",".join(str(x) for x in q_emb) + "]"
    try:
        return db.execute(text(f"""
            SELECT {_ITEM_COLUMNS},
                   1 - (i.embedding <=> CAST(:qvec AS vector)) AS cosine
            FROM items i
            WHERE i.user_id = :uid AND i.status = 'ready'
              AND i.embedding IS NOT NULL
              {_cat_filter(category, 'i.')}
            ORDER BY i.embedding <=> CAST(:qvec AS vector)
            LIMIT :lim
        """), {"uid": str(user_id), "qvec": vec_str,
               "cat": category, "lim": limit}).fetchall()
    except Exception as e:
        log.warning("[search] vector failed: %s", e)
        return []


def _content_key(row) -> str:
    """Identity used to collapse repeat saves of one piece of content (Phase 3).

    Rows are keyed by `content_id`, so two item rows a user saved under
    different URLs that resolved to the same ContentAsset appear once. Rows
    with no asset (NULL content_id) fall back to their item id, so unrelated
    legacy rows are never collapsed into each other.
    """
    content_id = row[_CONTENT_IDX]
    return str(content_id) if content_id is not None else f"item:{row[0]}"


def _dedupe_rows(rows) -> list:
    """Keep the first row per piece of content, preserving the given order."""
    seen = set()
    kept = []
    for row in rows:
        key = _content_key(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept

async def chunk_search(db: Session, user_id, query: str, limit: int = 50,
                       q_emb=None):
    """Find the chunks a query matches, not just the memories it matches.

    The item's own embedding is built from its title and brief, so a query
    about something the title never mentions scores badly against it. The chunks
    are the content itself, so they are where that query actually lands.

    Returns the item rows plus the winning chunk, so the caller can say *what*
    matched and *when* it was said.

    `q_emb` is the query vector when the caller already has one. `hybrid_search`
    embeds the query once for the item-vector list and hands the same vector
    here, rather than paying for the same string twice.
    """
    if not query.strip():
        return []
    # Called through the module, not imported by name: a test (or a future
    # provider swap) can then replace it without reloading this module.
    if q_emb is None:
        q_emb = await embedder.embed_text(query, task="query")
    if q_emb is None:
        return []
    vec_str = "[" + ",".join(str(x) for x in q_emb) + "]"
    try:
        return db.execute(text("""
            SELECT i.id, i.title_clean, i.summary, i.tags, i.category,
                   i.source_domain, i.thumbnail_url, i.created_at, i.content_id,
                   1 - (c.embedding <=> CAST(:qvec AS vector)) AS cosine,
                   c.chunk_text, c.chunk_idx, c.start_timestamp, c.start_seconds
            FROM chunks c
            JOIN items i ON i.id = c.item_id
            WHERE i.user_id = :uid AND i.status = 'ready'
            ORDER BY c.embedding <=> CAST(:qvec AS vector)
            LIMIT :lim
        """), {"uid": str(user_id), "qvec": vec_str, "lim": limit}).fetchall()
    except Exception as e:  # a vector outage must not take search down
        log.warning("[search] chunk search failed: %s", e)
        return []



def _ranks(rows) -> dict[str, int]:
    """Map item id -> rank (1-based) for one candidate list.

    Kept separate from the weight so a rank map cannot confuse an item id with
    its own bookkeeping.
    """
    return {str(row[0]): index + 1 for index, row in enumerate(rows)}


def metadata_filter(source_domain: str | None, saved_after: datetime | None,
                    saved_before: datetime | None) -> dict:
    """Metadata a user can filter by: where it came from and when it was saved.

    Applied after retrieval rather than inside it, so every candidate list is
    filtered identically and no list can smuggle in a row the others excluded.
    """
    meta: dict = {}
    if source_domain:
        meta["source_domain"] = source_domain
    if saved_after is not None:
        meta["after"] = saved_after
    if saved_before is not None:
        meta["before"] = saved_before
    return meta


def _filter_rows(rows, meta: dict) -> list:
    """Drop candidates that do not match the metadata filter."""
    if not meta or not rows:
        return list(rows)
    domain = meta.get("source_domain")
    after = meta.get("after")
    before = meta.get("before")
    kept = []
    for row in rows:
        if domain and row[5] != domain:
            continue
        created = row[7]
        if created is not None:
            if after is not None and created < after:
                continue
            if before is not None and created > before:
                continue
        kept.append(row)
    return kept


async def hybrid_search(db: Session, user_id, query: str, category: str = None,
                        limit: int = 10, source_domain: str = None,
                        saved_after: datetime = None,
                        saved_before: datetime = None):
    """Find the user's own memories. Retrieval only: nothing is recommended.

    Five candidate lists are merged with Reciprocal Rank Fusion and then
    nudged by recency:

        items by meaning        items by words
        chunks by meaning       chunks by words
        the user's own note and intent

    RRF is used rather than raw score sums because the lists are on
    incomparable scales (cosine vs ts_rank), and because a rank is exactly what
    each list can honestly contribute.
    """
    t0 = time.time()
    terms = query_terms(query)
    # Embed once. task="query" matters for Gemini, which trains separate
    # document and query vector spaces.
    # Called through the module rather than imported by name, so a test (or a
    # future provider swap) can replace the call without reloading this module.
    q_emb = await embedder.embed_text(query, task="query") if query.strip() else None

    meta = metadata_filter(source_domain, saved_after, saved_before)
    lex_rows = _filter_rows(lexical_search(db, user_id, query, terms, category),
                            meta)
    vec_rows = _filter_rows(vector_search(db, user_id, q_emb, category), meta)
    chunk_vec_rows = _filter_rows(
        await chunk_search(db, user_id, query, q_emb=q_emb), meta)
    chunk_lex_rows = _filter_rows(
        chunk_lexical_search(db, user_id, query, terms), meta)
    note_rows = _filter_rows(note_search(db, user_id, terms), meta)

    # The whole searchable document per item, so the reason can be checked
    # against everything that was actually indexed -- structured_data and
    # entities included, not just the title.
    documents = _documents(db, user_id,
                           list({str(r[0]) for r in
                                 lex_rows + vec_rows + chunk_vec_rows
                                 + chunk_lex_rows + note_rows}))

    if not any((lex_rows, vec_rows, chunk_vec_rows, chunk_lex_rows,
                note_rows)):
        took = int((time.time() - t0) * 1000)
        return [], took

    rows_by_id: dict[str, tuple] = {}
    # The best chunk per item, per list: several chunks of one video are one
    # memory to the user, not several results.
    chunk_by_id: dict[str, tuple] = {}
    for rows in (lex_rows, vec_rows, chunk_vec_rows, chunk_lex_rows,
                 note_rows):
        for row in rows:
            key = str(row[0])
            rows_by_id.setdefault(key, tuple(row[:9]))
            if len(row) > 13:
                chunk_by_id.setdefault(key, row)

    # (rank map, weight) per candidate list, in the order their weights are set.
    sources = [
        (_ranks(lex_rows), W_BM25),
        (_ranks(vec_rows), W_VEC),
        (_ranks(chunk_vec_rows), W_CHUNK_VEC),
        (_ranks(chunk_lex_rows), W_CHUNK_LEX),
        (_ranks(note_rows), W_NOTE_LEX),
    ]

    contexts = _with_notes(db, user_id,
                           list({str(r[8]) for r in rows_by_id.values()
                                 if r[8] is not None}))

    scored = []
    for iid, row in rows_by_id.items():
        score = sum(weight * (1.0 / (RRF_K + rank[iid]))
                    for rank, weight in sources if iid in rank)
        score += W_RECENCY * recency_boost(row[7])
        scored.append((iid, score, row))
    scored.sort(key=lambda x: x[1], reverse=True)

    results, seen_content = [], set()
    for iid, score, row in scored:
        key = _content_key(row)
        if key in seen_content:
            continue
        seen_content.add(key)
        chunk = chunk_by_id.get(iid)
        context = contexts.get(str(row[8])) if row[8] is not None else None
        results.append({"row": row, "score": score,
                        "match_reason": evidence_reason(
                            terms, row,
                            (context or {}).get("note"), chunk,
                            document=documents.get(iid),
                            intent=(context or {}).get("intent")),
                        "matched_chunk": chunk[10] if chunk else None,
                        "matched_at": chunk[12] if chunk else None})
        if len(results) >= limit:
            break
    took = int((time.time() - t0) * 1000)
    return results, took


