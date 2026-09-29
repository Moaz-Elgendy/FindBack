import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, nullable=False)
    auth_provider = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Item(Base):
    __tablename__ = "items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    fetch_metadata = Column(JSON, default=dict, nullable=False)
    failure_reason = Column(Text)
    summary = Column(Text)
    key_points = Column(JSON, default=list, nullable=False)
    category = Column(String(32))
    entities = Column(JSON, default=dict, nullable=False)
    intent = Column(String(32))
    tags = Column(ARRAY(String), default=list, nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    embedding = Column(Vector(1536))
    embedding_model = Column(String(64))
    tsv = Column(TSVECTOR)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "canonical_url", name="items_user_canonical_uq"),
        Index("items_user_created_idx", "user_id", "created_at"),
        Index("items_category_idx", "category"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_idx = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)

    __table_args__ = (
        UniqueConstraint("item_id", "chunk_idx", name="chunks_item_idx_uq"),
        Index("chunks_item_idx", "item_id"),
    )
