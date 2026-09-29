"""Initial FindBack schema."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table("users", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("auth_provider", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("items", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("url", sa.Text(), nullable=False), sa.Column("canonical_url", sa.Text(), nullable=False), sa.Column("title", sa.Text()), sa.Column("title_clean", sa.Text()), sa.Column("source_domain", sa.String(253)), sa.Column("source_type", sa.String(32)), sa.Column("thumbnail_url", sa.Text()), sa.Column("raw_s3_key", sa.Text()), sa.Column("raw_preview", sa.Text()), sa.Column("fetch_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("failure_reason", sa.Text()), sa.Column("summary", sa.Text()), sa.Column("key_points", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False), sa.Column("category", sa.String(32)), sa.Column("entities", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False), sa.Column("intent", sa.String(32)), sa.Column("tags", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::text[]"), nullable=False), sa.Column("status", sa.String(16), server_default="pending", nullable=False), sa.Column("embedding", Vector(1536)), sa.Column("embedding_model", sa.String(64)), sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("user_id", "canonical_url", name="items_user_canonical_uq"))
    op.execute("ALTER TABLE items ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title_clean,'') || ' ' || coalesce(summary,'') || ' ' || array_to_string(tags,' '))) STORED")
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
