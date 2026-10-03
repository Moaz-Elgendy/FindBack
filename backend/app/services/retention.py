"""Retention and deletion of a user's content (Phase 14).

Explicit, because "we will delete it eventually" is not a policy anyone can
verify. Three lifetimes, and they are different on purpose:

    raw text          the fetched page, transcript or raw snapshot. Kept only
                      as long as processing needs it, then dropped.
    derived text      the brief, chunks, vectors. Kept while the memory exists,
                      because this is what makes the memory findable.
    user context      the note and intent. Removed the moment the user asks,
                      without touching anything above.

Deleting a save deletes the user's data: their item rows, their memories, and
the derived text that belongs only to them. What is NOT deleted is the
`ContentAsset`, because it may be shared public content that another user still
points at (PRODUCT.md rule 3).
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.env import RAW_TEXT_RETENTION_HOURS

log = logging.getLogger("findback.retention")

# How long a fetched page is kept, read once at import from the environment.
# Zero: drop it as soon as the pipeline is done. See docs/OPERATIONS.md for the
# full retention policy.


def retention_policy() -> dict:
    """The policy in force, as data. Readable by operators and by tests."""
    return {
        "raw_text_hours": RAW_TEXT_RETENTION_HOURS,
        "derived_text": "kept while the memory exists",
        "user_context": "kept until the user removes it, or the save is deleted",
        "asset": "shared; survives deletion of one user's save",
    }


def purge_raw_text(db: Session, item_ids: list[str] | None = None) -> int:
    """Drop the raw fetched text, keeping the brief and chunks.

    Returns the number of items cleaned. Safe to call repeatedly.
    """
    sql = ("UPDATE items SET raw_text = NULL, normalized_text = NULL, "
           "chunk_texts = NULL WHERE raw_text IS NOT NULL")
    params = {}
    if item_ids:
        sql += " AND id = ANY(:ids)"
        params["ids"] = item_ids
    result = db.execute(text(sql), params)
    db.commit()
    count = result.rowcount or 0
    if count:
        log.info("[retention] dropped raw text for %d item(s)", count)
    return count


def delete_save(db: Session, user_id, content_id=None,
                item_ids: list[str] | None = None) -> dict:
    """Delete one user's save of this content, and nothing of anyone else's.

    Returns a count per table so the caller (and the test) can see exactly what
    went. The ContentAsset is deliberately left alone.
    """
    counts = {"items": 0, "user_memories": 0, "chunks": 0}
    if item_ids:
        # Counted before the delete: chunks cascade from items, so afterwards
        # there is nothing left to count. The ids arrive as strings, hence the
        # casts -- `uuid = ANY(text[])` has no operator.
        counts["chunks"] = db.execute(text(
            "SELECT count(*) FROM chunks WHERE item_id::text = ANY(:ids)"),
            {"ids": list(item_ids)}).scalar() or 0
        result = db.execute(text(
            "DELETE FROM items WHERE user_id = :u AND id::text = ANY(:ids)"),
            {"u": str(user_id), "ids": list(item_ids)})
        counts["items"] = result.rowcount or 0
        result = db.execute(text(
            "DELETE FROM user_memories WHERE user_id = :u "
            "AND content_id = :c"), {"u": str(user_id), "c": str(content_id)})
        counts["user_memories"] = result.rowcount or 0
    else:
        # Phase 16: the ITEM first, then the memory. `items_user_content_fk`
        # points at `user_memories` with no ON DELETE clause, so the reverse
        # order is refused by the database -- which is the intended behaviour:
        # removing a memory must never silently take the user's item rows (and
        # the derived text in them) with it. Both statements run in one
        # transaction, so a failure in either leaves the save intact.
        result = db.execute(text(
            "DELETE FROM items WHERE user_id = :u AND content_id = :c"),
            {"u": str(user_id), "c": str(content_id)})
        counts["items"] = result.rowcount or 0
        result = db.execute(text(
            "DELETE FROM user_memories WHERE user_id = :u AND content_id = :c"),
            {"u": str(user_id), "c": str(content_id)})
        counts["user_memories"] = result.rowcount or 0
    db.commit()
    log.info("[retention] deleted a save for user %s: %s", user_id, counts)
    return counts


def expired_raw_cutoff(now: datetime.datetime | None = None) -> datetime.datetime:
    """When raw text older than this must go."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now - datetime.timedelta(hours=RAW_TEXT_RETENTION_HOURS)


def purge_expired_raw_text(db: Session,
                           now: datetime.datetime | None = None) -> int:
    """Apply the retention window to rows that are already past it."""
    cutoff = expired_raw_cutoff(now)
    result = db.execute(text(
        "UPDATE items SET raw_text = NULL, normalized_text = NULL, "
        "chunk_texts = NULL WHERE processed_at IS NOT NULL "
        "AND processed_at < :cutoff AND raw_text IS NOT NULL"),
        {"cutoff": cutoff})
    db.commit()
    count = result.rowcount or 0
    if count:
        log.info("[retention] dropped expired raw text for %d item(s)", count)
    return count