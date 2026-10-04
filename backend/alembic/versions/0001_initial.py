"""Initial FindBack schema.

Migration strategy (Phase 0.5 schema repair)
--------------------------------------------
This file is repaired **in place** rather than superseded by a new revision.
The original `tsv` expression used the builtin `array_to_string`, which
PostgreSQL marks STABLE, so a STORED generated column could never be created
from it. `alembic upgrade head` therefore failed on *every* Postgres
(PG16 and PG18 both reject it), and so did `SCHEMA_BOOTSTRAP=create`.

Because revision 0001 never applied anywhere, there is no deployed database
whose history this edit would falsify: no existing installation could have
reached `head` through it. Editing the unreleased baseline is the honest fix;
adding a 0002 would leave a permanently broken first step that every fresh
database must still execute. The alternative repair (dropping `tags` from the
indexed text) is deliberately rejected, because it would silently remove tag
terms from BM25 recall.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Mirrors IMMUTABLE_ARRAY_TO_STRING_SQL in app/models.py. It must stay
# byte-identical; tests/test_schema_parity.py compares the two.
IMMUTABLE_ARRAY_TO_STRING_SQL = """
CREATE OR REPLACE FUNCTION immutable_array_to_string(arr text[], sep text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
STRICT
AS $fn$
DECLARE
  out text := '';
  idx integer;
  lo  integer;
  hi  integer;
BEGIN
  -- STRICT already returns NULL for a NULL array, matching the builtin.
  -- An EMPTY array must yield '' (not NULL), which is why the loop below
  -- starts from an empty string instead of short-circuiting on a NULL lower
  -- bound: array_lower('{}') is NULL, yet the correct answer is ''.
  lo := array_lower(arr, 1);
  hi := array_upper(arr, 1);
  IF lo IS NULL THEN
    RETURN out;
  END IF;
  FOR idx IN lo..hi LOOP
    IF idx > lo THEN
      out := out || sep;
    END IF;
    out := out || arr[idx];
  END LOOP;
  RETURN out;
END;
$fn$;
"""


def upgrade():
    op.execute(IMMUTABLE_ARRAY_TO_STRING_SQL)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table("users", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("auth_provider", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("items", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("url", sa.Text(), nullable=False), sa.Column("canonical_url", sa.Text(), nullable=False), sa.Column("title", sa.Text()), sa.Column("title_clean", sa.Text()), sa.Column("source_domain", sa.String(253)), sa.Column("source_type", sa.String(32)), sa.Column("thumbnail_url", sa.Text()), sa.Column("raw_s3_key", sa.Text()), sa.Column("raw_preview", sa.Text()), sa.Column("fetch_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("failure_reason", sa.Text()), sa.Column("summary", sa.Text()), sa.Column("key_points", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False), sa.Column("category", sa.String(32)), sa.Column("entities", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("intent", sa.String(32)), sa.Column("tags", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::text[]"), nullable=False), sa.Column("status", sa.String(16), server_default="pending", nullable=False), sa.Column("embedding", Vector(1536)), sa.Column("embedding_model", sa.String(64)), sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("user_id", "canonical_url", name="items_user_canonical_uq"))
    op.execute("ALTER TABLE items ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title_clean,'') || ' ' || coalesce(summary,'') || ' ' || immutable_array_to_string(tags,' '))) STORED")
    op.create_index("items_user_created_idx", "items", ["user_id", "created_at"])
    op.create_index("items_category_idx", "items", ["category"])
    op.execute("CREATE INDEX items_tsv_idx ON items USING GIN (tsv)")
    op.execute("CREATE INDEX items_embedding_hnsw ON items USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)")
    op.create_table("chunks", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False), sa.Column("chunk_idx", sa.Integer(), nullable=False), sa.Column("chunk_text", sa.Text(), nullable=False), sa.Column("embedding", Vector(1536), nullable=False), sa.UniqueConstraint("item_id", "chunk_idx", name="chunks_item_idx_uq"))
    op.create_index("chunks_item_idx", "chunks", ["item_id"])
    op.execute("CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    op.drop_table("chunks")
    op.drop_table("items")
    op.drop_table("users")
    # Only after every dependent generated column is gone.
    op.execute("DROP FUNCTION IF EXISTS immutable_array_to_string(text[], text)")
