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
            "AND content_id = :c"), {"u": str(user_id), "c": str(content_id) if content_id is not None else None})
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
    # Account-owned/shared assets retain the existing lifetime. Guest copies
    # are temporary and may go once their last save and worker are gone.
    if content_id is not None:
        guest_asset = db.execute(text("""SELECT a.id FROM content_assets a JOIN users u ON u.id=a.owner_user_id
            WHERE a.id=:c AND a.owner_user_id=:u AND a.visibility <> 'PUBLIC' AND u.auth_subject LIKE 'guest:%'
            FOR UPDATE OF a SKIP LOCKED"""), {"c": content_id, "u": user_id}).first()
        jobs = db.execute(text("SELECT id FROM processing_jobs WHERE content_id=:c FOR UPDATE SKIP LOCKED"),
                          {"c": content_id}).all() if guest_asset else []
        total = db.execute(text("SELECT count(*) FROM processing_jobs WHERE content_id=:c"),
                           {"c": content_id}).scalar() if guest_asset else -1
        if guest_asset and len(jobs) == total:
            db.execute(text("""DELETE FROM content_assets a USING users u
            WHERE a.id=:c AND a.owner_user_id=:u AND a.visibility <> 'PUBLIC' AND u.id=a.owner_user_id
              AND u.auth_subject LIKE 'guest:%'
              AND NOT EXISTS (SELECT 1 FROM items WHERE content_id=a.id)
              AND NOT EXISTS (SELECT 1 FROM user_memories WHERE content_id=a.id)
              AND NOT EXISTS (SELECT 1 FROM processing_jobs WHERE content_id=a.id
                              AND (status='PROCESSING' OR locked_at IS NOT NULL))"""),
                       {"c": content_id, "u": user_id})
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

def purge_expired_guest_staging(db: Session, limit: int = 50) -> int:
    """Expire only guest staging, preserving assets referenced by other saves.

    Active workers finish before cleanup; their PROCESSING job prevents a
    deletion race. Asset/job locks fence concurrent saves and dispatchers.
    """
    from app.services.auth_tokens import guest_retention_hours
    rows = db.execute(text("""
        SELECT i.id, i.user_id, i.content_id FROM items i JOIN users u ON u.id=i.user_id
        WHERE u.auth_subject LIKE 'guest:%'
          AND i.deleted_at IS NULL
          AND i.created_at < now() - make_interval(hours => :hours)
        ORDER BY i.created_at LIMIT :limit FOR UPDATE OF i SKIP LOCKED
    """), {"hours": guest_retention_hours(), "limit": limit}).all()
    removed = 0
    for item_id, user_id, content_id in rows:
        if content_id is not None:
            asset = db.execute(text("SELECT id, owner_user_id FROM content_assets WHERE id=:id FOR UPDATE SKIP LOCKED"),
                               {"id": content_id}).first()
            if asset is None:
                continue
            jobs = db.execute(text("SELECT id, status, locked_at FROM processing_jobs WHERE content_id=:id FOR UPDATE SKIP LOCKED"),
                              {"id": content_id}).all()
            total = db.execute(text("SELECT count(*) FROM processing_jobs WHERE content_id=:id"),
                               {"id": content_id}).scalar()
            if len(jobs) != total or any(j.status == "PROCESSING" or j.locked_at is not None for j in jobs):
                continue
        db.execute(text("DELETE FROM items WHERE id=:id AND user_id=:u"), {"id": item_id, "u": user_id})
        if content_id is not None:
            db.execute(text("""DELETE FROM user_memories WHERE user_id=:u AND content_id=:c
                AND NOT EXISTS (SELECT 1 FROM items WHERE user_id=:u AND content_id=:c)"""),
                       {"u": user_id, "c": content_id})
            # Only an asset owned by this guest may be garbage collected.
            db.execute(text("""DELETE FROM content_assets WHERE id=:c AND owner_user_id=:u AND visibility <> 'PUBLIC'
                AND NOT EXISTS (SELECT 1 FROM items WHERE content_id=:c)
                AND NOT EXISTS (SELECT 1 FROM user_memories WHERE content_id=:c)"""),
                       {"u": user_id, "c": content_id})
        removed += 1
    # A save deleted during processing leaves its asset until the worker ends.
    # Collect that temporary copy too; it no longer has an item to drive expiry.
    orphans = db.execute(text("""
        SELECT a.id FROM content_assets a JOIN users u ON u.id=a.owner_user_id
        WHERE u.auth_subject LIKE 'guest:%' AND a.visibility <> 'PUBLIC'
          AND a.created_at < now() - make_interval(hours => :hours)
          AND NOT EXISTS (SELECT 1 FROM items WHERE content_id=a.id)
          AND NOT EXISTS (SELECT 1 FROM user_memories WHERE content_id=a.id)
        ORDER BY a.created_at LIMIT :limit FOR UPDATE OF a SKIP LOCKED
    """), {"hours": guest_retention_hours(), "limit": limit}).all()
    for (content_id,) in orphans:
        jobs = db.execute(text("SELECT id, status, locked_at FROM processing_jobs WHERE content_id=:id FOR UPDATE SKIP LOCKED"),
                          {"id": content_id}).all()
        total = db.execute(text("SELECT count(*) FROM processing_jobs WHERE content_id=:id"),
                           {"id": content_id}).scalar()
        if len(jobs) != total or any(j.status == "PROCESSING" or j.locked_at is not None for j in jobs):
            continue
        db.execute(text("""DELETE FROM content_assets WHERE id=:id AND visibility <> 'PUBLIC'
            AND NOT EXISTS (SELECT 1 FROM items WHERE content_id=:id)
            AND NOT EXISTS (SELECT 1 FROM user_memories WHERE content_id=:id)"""), {"id": content_id})
    db.commit()
    return removed
