"""Split content from a user save: ContentAsset + UserMemory.

Migration strategy (Phase 1)
---------------------------
New revision, so the previous `0001_initial` history stays untouched.

The backfill is deliberately one asset per existing item and one memory per
existing item. Nothing is merged or de-duplicated here: PRODUCT.md rule 3 says a
duplicate URL does NOT mean the content is safe to share between users, and
cross-user reuse is Phase 2's job (it adds the UNIQUE constraint on
dedupe_key). Merging now would silently attribute one user's saves to another.

Nothing is deleted. `items` keeps every column it had and stays the table the
existing search/retrieve paths read, so save and retrieval behave exactly as
before. `items.content_id` only records which asset a row was split out of.

Pairing
-------
Each asset reuses the source row's `items.id` as its own primary key. That
makes the 1:1 mapping exact and reversible: no heuristic join on mutable text
columns is needed, and two users who saved the same URL still get two distinct
assets (correct for Phase 1).

Field mapping
-------------
items.status            -> content_assets.processing_status  (same value set)
items.failure_reason    -> content_assets.processing_error
items.raw_s3_key        -> content_assets.raw_content         (a reference)
items.title_clean/title -> content_assets.title
items.summary           -> content_assets.brief
items.key_points        -> content_assets.structured_data{"key_points": ...}
items.entities          -> content_assets.entities
items.tags              -> content_assets.topics
items.intent            -> content_assets.intent
items.created_at        -> first_saved_at / created_at
items.last_seen_at      -> last_saved_at
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_content_asset_user_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("dedupe_key", sa.Text()),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32)),
        sa.Column("visibility", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("processing_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("processing_error", sa.Text()),
        sa.Column("raw_content", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("brief", sa.Text()),
        sa.Column("structured_data", postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("entities", postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("topics", postgresql.ARRAY(sa.String()),
                  server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("intent", sa.String(32)),
        sa.Column("pipeline_version", sa.String(32)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("content_assets_canonical_url_idx", "content_assets", ["canonical_url"])
    op.create_index("content_assets_processing_status_idx", "content_assets", ["processing_status"])

    op.create_table(
        "user_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_note", sa.Text()),
        sa.Column("user_intent", sa.String(32)),
        sa.Column("first_saved_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_saved_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("save_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "content_id", name="user_memories_user_content_uq"),
    )
    op.create_index("user_memories_user_saved_idx", "user_memories", ["user_id", "last_saved_at"])
# Added after both target tables exist so the backfill has somewhere to point.
    op.add_column("items", sa.Column("content_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("items_content_id_fk", "items", "content_assets",
                          ["content_id"], ["id"], ondelete="SET NULL")

    # One ContentAsset per existing item. The asset reuses items.id so the
    # pairing is exact rather than inferred. dedupe_key is deliberately left
    # NULL: Phase 2 owns the key format and the UNIQUE constraint.
    op.execute("""
        INSERT INTO content_assets (
            id, canonical_url, source_type, visibility, processing_status,
            processing_error, raw_content, title, brief, structured_data,
            entities, topics, intent, processed_at, created_at, updated_at
        )
        SELECT
            i.id,
            i.canonical_url,
            i.source_type,
            'unknown',
            i.status,
            i.failure_reason,
            i.raw_s3_key,
            COALESCE(i.title_clean, i.title),
            i.summary,
            jsonb_build_object('key_points', i.key_points, 'category', i.category),
            i.entities,
            i.tags,
            i.intent,
            i.processed_at,
            i.created_at,
            COALESCE(i.processed_at, i.created_at)
        FROM items i
    """)

    # One UserMemory per existing item, pointed at the asset that replaced it.
    # save_count is 1 because `items` never recorded repeat saves, only a
    # last_seen_at timestamp.
    op.execute("""
        INSERT INTO user_memories (
            id, user_id, content_id, first_saved_at, last_saved_at,
            save_count, created_at, updated_at
        )
        SELECT
            i.id,
            i.user_id,
            a.id,
            i.created_at,
            i.last_seen_at,
            1,
            i.created_at,
            COALESCE(i.processed_at, i.last_seen_at)
        FROM items i
        JOIN content_assets a ON a.id = i.id
    """)

    # Record the linkage on the row the existing endpoints still read.
    op.execute("UPDATE items SET content_id = i.id FROM items i WHERE items.id = i.id")


def downgrade():
    op.drop_column("items", "content_id")
    op.drop_table("user_memories")
    op.drop_table("content_assets")