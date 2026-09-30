import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.sql import func

from app.database import Base

GEN_RANDOM_UUID = text("gen_random_uuid()")

# The tsvector expression must stay byte-identical to the Alembic baseline in
# alembic/versions/0001_initial.py, otherwise create_all and `alembic upgrade
# head` produce divergent schemas. tests/test_schema_parity.py enforces this.
TSV_EXPRESSION = (
    "to_tsvector('english', coalesce(title_clean,'') || ' ' "
    "|| coalesce(summary,'') || ' ' || array_to_string(tags,' '))"
)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    email = Column(String(320), unique=True, nullable=False)
    auth_provider = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Item(Base):
    __tablename__ = "items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    canonical_url = Column(Text, nullable=False)
    title = Column(Text)
    title_clean = Column(Text)
    source_domain = Column(String(253))
    source_type = Column(String(32))
    thumbnail_url = Column(Text)
    raw_s3_key = Column(Text)
    raw_preview = Column(Text)
    fetch_metadata = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    failure_reason = Column(Text)
    summary = Column(Text)
    key_points = Column(JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False)
    category = Column(String(32))
    entities = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    intent = Column(String(32))
    tags = Column(ARRAY(String), default=list, server_default=text("'{}'::text[]"), nullable=False)
    status = Column(String(16), default="pending", server_default="pending", nullable=False)
    embedding = Column(Vector(1536))
    embedding_model = Column(String(64))
    # Server-generated: never written by the ORM. PG marks the 2-arg
    # to_tsvector(regconfig, text) IMMUTABLE, which is what makes STORED legal.
    tsv = Column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "canonical_url", name="items_user_canonical_uq"),
        Index("items_user_created_idx", "user_id", "created_at"),
        Index("items_category_idx", "category"),
        Index("items_tsv_idx", "tsv", postgresql_using="gin"),
        # pgvector HNSW defaults are m=16 / ef_construction=64 — same as the migration.
        Index("items_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_idx = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)

    __table_args__ = (
        UniqueConstraint("item_id", "chunk_idx", name="chunks_item_idx_uq"),
        Index("chunks_item_idx", "item_id"),
        Index("chunks_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

