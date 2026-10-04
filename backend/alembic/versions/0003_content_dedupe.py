"""Deduplicate content: one ContentAsset per piece of content (Phase 2).

Migration strategy
------------------
New revision 0003, applied on top of 0002. The UNIQUE constraint is added to
the existing column rather than by rewriting 0002, because 0002 is a shipped
migration that real databases have already applied.

Backfill before constraint
--------------------------
Phase 1 deliberately created one asset per saved item, so a URL two users both
saved currently has two assets. Those must be merged before a UNIQUE index can
be added, or the index build fails. The merge keeps one asset per dedupe_key and
repoints every reference at it:

* `items.content_id`      -> the surviving asset
* `user_memories.content_id` -> the surviving asset

No user-visible data is dropped: the surviving asset keeps its content fields,
and user memories are *re-pointed*, never deleted, so each user's notes,
timestamps and save counts survive. Where the losers held richer extraction
fields that the survivor lacks, those are merged onto the survivor first.

dedupe_key is filled from the Phase 2 order (platform id, canonical URL,
normalized content hash). Assets whose key cannot be computed keep NULL;
PostgreSQL treats NULLs as distinct, so they do not block the index.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_content_dedupe"
down_revision = "0002_content_asset_user_memory"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Fill dedupe_key using the SAME function the application uses, so the
    #    backfill and runtime can never disagree about what a key is.
    bind = op.get_bind()
    from app.utils.dedupe import dedupe_key_for_row as dedupe_key_for_asset

    rows = bind.execute(sa.text(
        "SELECT id, canonical_url, title, brief, structured_data FROM content_assets"
    )).mappings().all()
    for row in rows:
        structured = row["structured_data"] or {}
        key_points = structured.get("key_points") or []
        key = dedupe_key_for_asset(url=row["canonical_url"] or "",
                                   title=row["title"] or "",
                                   summary=row["brief"] or "",
                                   key_points=key_points)
        if key:
            bind.execute(sa.text(
                "UPDATE content_assets SET dedupe_key = :k WHERE id = :id"),
                {"k": key, "id": row["id"]})

    # 2. Merge assets that now share a dedupe_key, keeping the earliest.
    #    COALESCE carries the loser's richer fields onto the survivor instead
    #    of discarding them.
    op.execute("""
        UPDATE content_assets survivor
        SET brief = COALESCE(survivor.brief, loser.brief),
            title = COALESCE(survivor.title, loser.title),
            raw_content = COALESCE(survivor.raw_content, loser.raw_content),
            processing_error = COALESCE(survivor.processing_error, loser.processing_error),
            processed_at = COALESCE(survivor.processed_at, loser.processed_at)
        FROM content_assets loser
        WHERE loser.dedupe_key = survivor.dedupe_key
          AND loser.dedupe_key IS NOT NULL
          AND loser.id <> survivor.id
          AND loser.created_at > survivor.created_at
    """)
    op.execute("""
        UPDATE items SET content_id = k.survivor_id
        FROM (
            SELECT loser.id AS loser_id,
                   (SELECT s.id FROM content_assets s
                     WHERE s.dedupe_key = loser.dedupe_key
                     ORDER BY s.created_at, s.id LIMIT 1) AS survivor_id
            FROM content_assets loser
            WHERE loser.dedupe_key IS NOT NULL
        ) k
        WHERE items.content_id = k.loser_id
    """)
    op.execute("""
        UPDATE user_memories SET content_id = k.survivor_id
        FROM (
            SELECT loser.id AS loser_id,
                   (SELECT s.id FROM content_assets s
                     WHERE s.dedupe_key = loser.dedupe_key
                     ORDER BY s.created_at, s.id LIMIT 1) AS survivor_id
            FROM content_assets loser
            WHERE loser.dedupe_key IS NOT NULL
        ) k
        WHERE user_memories.content_id = k.loser_id
    """)
    op.execute("""
        DELETE FROM content_assets a
        WHERE a.dedupe_key IS NOT NULL
          AND EXISTS (SELECT 1 FROM content_assets s
                      WHERE s.dedupe_key = a.dedupe_key
                        AND (s.created_at, s.id) < (a.created_at, a.id))
    """)

    # 3. Now the UNIQUE constraint the phase requires. A plain unique index,
    #    not an if-exists check: the database itself must prevent duplicates.
    op.create_unique_constraint("content_assets_dedupe_key_uq",
                                "content_assets", ["dedupe_key"])


def downgrade():
    op.drop_constraint("content_assets_dedupe_key_uq", "content_assets",
                       type_="unique")