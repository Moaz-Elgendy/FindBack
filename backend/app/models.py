import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, Column, Computed, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

from app.database import Base

GEN_RANDOM_UUID = text("gen_random_uuid()")

# `array_to_string` is STABLE in PostgreSQL, and a STORED generated column may
# only call IMMUTABLE functions, so the original expression here could never be
# created -- `alembic upgrade head` and `SCHEMA_BOOTSTRAP=create` both failed on
# every Postgres version. This wrapper joins a text[] with the separator using
# only immutable operations (array_lower, array_upper, subscript, ||), so
# declaring it IMMUTABLE is truthful rather than a cast, and the indexed text
# still covers title_clean + summary + tags.
#
# Kept byte-identical to IMMUTABLE_ARRAY_TO_STRING_SQL in
# alembic/versions/0001_initial.py; tests/test_schema_parity.py compares them.
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

# The tsvector expression must stay byte-identical to the Alembic baseline in
# alembic/versions/0001_initial.py, otherwise create_all and `alembic upgrade
# head` produce divergent schemas. tests/test_schema_parity.py enforces this.
TSV_EXPRESSION = (
    "to_tsvector('english', coalesce(title_clean,'') || ' ' "
    "|| coalesce(summary,'') || ' ' || immutable_array_to_string(tags,' '))"
)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    email = Column(String(320), unique=True, nullable=False)
    # Phase 16: the issuer's stable identifier for this person. Identity is keyed
    # on this rather than on `email`, because an address can be reassigned and
    # the next holder must not inherit the previous user's library.
    #
    # Nullable on purpose: an account created before this column exists cannot
    # be bound until its owner next presents a token, and PostgreSQL treats NULLs
    # as distinct, so unbound accounts never collide with each other.
    auth_subject = Column(String(255))
    auth_provider = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Named explicitly rather than via `unique=True` on the column, so this
    # produces the same constraint name as migration 0010 and the two schema
    # paths cannot drift.
    __table_args__ = (
        UniqueConstraint("auth_subject", name="users_auth_subject_uq"),
    )


class WeeklyNotePreference(Base):
    __tablename__ = 'weekly_note_preferences'
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    weekday = Column(Integer, nullable=False, default=6, server_default='6')
    hour = Column(Integer, nullable=False, default=18, server_default='18')
    minute = Column(Integer, nullable=False, default=0, server_default='0')
    time_zone = Column(String(100), nullable=False, default='UTC', server_default='UTC')


class DeletedIdentity(Base):
    # Old signed JWTs remain valid at the issuer until expiry; never recreate their user.
    __tablename__ = 'deleted_identities'
    subject_hash = Column(String(64), primary_key=True)


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
    # Phase 1: the asset this row was split out of. Nullable so the column can
    # be added to existing rows before the backfill runs, and so an item created
    # by a failed insert still has a place to point.
    content_id = Column(UUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="SET NULL"))
    fetch_metadata = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    failure_reason = Column(Text)
    reprocess_snapshot = Column(JSONB)
    reprocess_failure = Column(Text)
    edited_title = Column(Text)
    edited_summary = Column(Text)
    link_only = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    evidence_bundle = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    brief_v2 = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    processing_metadata = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    needs_retry = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    summary = Column(Text)
    key_points = Column(JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False)
    category = Column(String(32))
    entities = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    intent = Column(String(32))
    tags = Column(ARRAY(String), default=list, server_default=text("'{}'::text[]"), nullable=False)
    status = Column(String(16), default="pending", server_default="pending", nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    # Phase 8: the last pipeline stage that completed. A retry resumes at the
    # stage AFTER this one, so earlier (and expensive) stages are not repeated.
    raw_text = Column(Text)
    normalized_text = Column(Text)
    chunk_texts = Column(ARRAY(Text))
    # Phase 11: the start time of each chunk, parallel to chunk_texts. A JSON
    # list of {"timestamp": str|None, "seconds": int|None}, or NULL.
    chunk_timestamps = Column(JSONB)
    embedding = Column(Vector(1536))
    embedding_model = Column(String(64))
    # Server-generated: never written by the ORM. PG marks the 2-arg
    # to_tsvector(regconfig, text) IMMUTABLE, which is what makes STORED legal.
    tsv = Column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))
    # Phase 12: everything about this memory that is worth matching words
    # against, flattened into one document: title, brief, structured_data,
    # entities and topics. Built by the pipeline, not computed, because
    # `entities`/`key_points` are JSONB and a STORED tsvector cannot call
    # jsonb functions (they are not IMMUTABLE).
    search_text = Column(Text)
    # Server-generated: never written by the ORM. `search_text` is plain text, so
    # unlike the JSONB columns on this table it can be indexed as a STORED
    # tsvector. `items.tsv` above is kept as the original narrower index.
    search_text_tsv = Column(TSVECTOR, Computed(
        "to_tsvector('english', coalesce(search_text,''))", persisted=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "canonical_url", name="items_user_canonical_uq"),
        UniqueConstraint("id", "user_id", name="items_id_user_uq"),
        # Phase 16: an item may only point at content THIS user has a memory of.
        # The application checks that on every route; this makes it impossible to
        # violate from any route, present or future.
        #
        # NO `ondelete`, and that is the point rather than an omission.
        # CASCADE would mean a deleted memory row silently took the user's item
        # rows with it, and those hold the derived text that exists nowhere else.
        # NO ACTION refuses the delete so the mistake is loud; the single caller
        # that removes a memory deletes the items first.
        ForeignKeyConstraint(
            ["user_id", "content_id"],
            ["user_memories.user_id", "user_memories.content_id"],
            name="items_user_content_fk"),
        Index("items_user_created_idx", "user_id", "created_at"),
        Index("items_category_idx", "category"),
        Index("items_deleted_at_idx", "deleted_at",
              postgresql_where=text("deleted_at IS NOT NULL")),
        Index("items_evidence_source_idx", text("(evidence_bundle->>'source_platform')"),
              text("(evidence_bundle->>'source_id')"),
              postgresql_where=text("evidence_bundle->>'evidence_level' = 'full_transcript'")),
        Index("items_tsv_idx", "tsv", postgresql_using="gin"),
        # Phase 12: the full searchable document, not just title+summary+tags.
        Index("items_search_text_tsv_idx", "search_text_tsv", postgresql_using="gin"),
        # pgvector HNSW defaults are m=16 / ef_construction=64 — same as the migration.
        Index("items_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


# PRODUCT.md rule 3. Phase 4: UNKNOWN is treated EXACTLY like PRIVATE, so the
# only value that may ever be shared between users is PUBLIC.
VISIBILITY_PUBLIC = "PUBLIC"
VISIBILITY_PRIVATE = "PRIVATE"
# Unknown means the content has not been classified, and unclassified content
# is private.
VISIBILITY_UNKNOWN = "UNKNOWN"
# Values that must never be shared across users. UNKNOWN is here on purpose:
# "unknown" is treated exactly like PRIVATE, not like PUBLIC.
NON_SHARED_VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_UNKNOWN)

# Phase 1 reuses the existing Item.status values so nothing new is introduced.
PROCESSING_PENDING = "pending"
PROCESSING_PROCESSING = "processing"
PROCESSING_READY = "ready"
PROCESSING_FAILED = "failed"


class ContentAsset(Base):
    """The content itself, independent of who saved it.

    One row per distinct piece of content. Reuse across users only becomes real
    in Phase 2 (dedupe_key gets a UNIQUE constraint there); in Phase 1 the
    backfill creates exactly one asset per existing item, so nothing is shared
    yet and every existing save keeps working unchanged.
    """

    __tablename__ = "content_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    # Populated but NOT yet unique: Phase 2 owns deduplication.
    dedupe_key = Column(Text)
    canonical_url = Column(Text, nullable=False)
    source_type = Column(String(32))
    visibility = Column(String(16), default=VISIBILITY_UNKNOWN,
                        server_default=VISIBILITY_UNKNOWN, nullable=False)
    # Phase 4: the user this asset belongs to when it is NOT public. NULL for a
    # PUBLIC asset, which anybody may reuse. This is what makes PRIVATE and
    # UNKNOWN content private -- without an owner there would be no way to tell
    # "my own copy" from "someone else's copy".
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    processing_status = Column(String(16), default=PROCESSING_PENDING,
                               server_default=PROCESSING_PENDING, nullable=False)
    processing_error = Column(Text)
    # Reference to the stored raw snapshot (the S3 key the worker already writes),
    # not the content itself.
    raw_content = Column(Text)
    title = Column(Text)
    brief = Column(Text)
    structured_data = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    entities = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    topics = Column(ARRAY(String), default=list, server_default=text("'{}'::text[]"), nullable=False)
    intent = Column(String(32))
    pipeline_version = Column(String(32))
    processed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Phase 4: uniqueness is privacy-scoped, so it cannot be a single
        # UNIQUE(dedupe_key) any more -- two users are each allowed their own
        # PRIVATE copy of the same URL.
        #   - a PUBLIC key is unique worldwide: anyone may reuse that one asset.
        #   - any other key is unique only per owner_user_id.
        # PostgreSQL partial unique indexes cannot be expressed as a
        # table-level UniqueConstraint, so they are declared here as Index objects
        # with postgresql_where and mirrored in migration 0004.
        Index("content_assets_public_dedupe_uq", "dedupe_key", unique=True,
              postgresql_where=text("visibility = 'PUBLIC'")),
        Index("content_assets_owner_dedupe_uq", "owner_user_id", "dedupe_key",
              unique=True, postgresql_where=text("visibility <> 'PUBLIC'")),
        Index("content_assets_canonical_url_idx", "canonical_url"),
        Index("content_assets_processing_status_idx", "processing_status"),
    )


class UserMemory(Base):
    """One user's private relationship to a ContentAsset.

    Never shared. Holds only what belongs to this user: their note, their
    intent, and their own save timestamps/count.
    """

    __tablename__ = "user_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content_id = Column(UUID(as_uuid=True), ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False)
    user_note = Column(Text)
    user_intent = Column(String(32))
    first_saved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_saved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    save_count = Column(Integer, default=1, server_default="1", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # One memory per user per asset. This mirrors the existing
        # items_user_canonical_uq guarantee at the new level.
        UniqueConstraint("user_id", "content_id", name="user_memories_user_content_uq"),
        Index("user_memories_user_saved_idx", "user_id", "last_saved_at"),
    )


# --- Phase 5: durable processing jobs (outbox) ---------------------------
# The job row is written in the same transaction as the content it refers to,
# so a save can never succeed while its processing intent is lost.

# Phase 6: the processing state machine, exactly these four states and no
# others. `dispatched` from Phase 5 is gone: handing work to the queue IS
# entering PROCESSING.
JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_PROCESSING = "PROCESSING"
JOB_STATUS_READY = "READY"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_PROCESSING,
                JOB_STATUS_READY, JOB_STATUS_FAILED)
# States in which a job still occupies its content: a second job for the same
# content and pipeline may not be created, and a second worker may not start.
JOB_ACTIVE_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_PROCESSING)

# job_type carries the pipeline version, so a later pipeline can re-process the
# same content with its own job instead of colliding with the old one.
JOB_TYPE_PROCESS = "process_item:v1"


class ProcessingJob(Base):
    """A durable intent to process one piece of content.

    Written in the same DB transaction as the ContentAsset, so if the Celery
    publish fails the intent survives and a dispatcher can retry it later.
    """

    __tablename__ = "processing_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=GEN_RANDOM_UUID)
    content_id = Column(UUID(as_uuid=True),
                        ForeignKey("content_assets.id", ondelete="CASCADE"),
                        nullable=False)
    job_type = Column(String(64), default=JOB_TYPE_PROCESS,
                      server_default=JOB_TYPE_PROCESS, nullable=False)
    status = Column(String(16), default=JOB_STATUS_PENDING,
                    server_default=JOB_STATUS_PENDING, nullable=False)
    attempt_count = Column(Integer, default=0, server_default="0", nullable=False)
    # When the dispatcher may next try this job; raised on failure (backoff).
    available_at = Column(DateTime(timezone=True), server_default=func.now(),
                          nullable=False)
    # Set while a dispatcher is publishing, so a second dispatcher skips it.
    locked_at = Column(DateTime(timezone=True))
    attempt_token = Column(UUID(as_uuid=True))
    # Phase 8: which pipeline stage completed last (FETCH, NORMALIZE,
    # UNDERSTAND, BRIEF, CHUNK or EMBED). Drives where a retry resumes.
    last_stage = Column(String(16))
    last_error = Column(Text)
    # Phase 18: when a worker first claimed this job. Nullable -- an existing
    # row has no claim time to recover, and inventing one would put fabricated
    # data into the queue_wait_time metric. Read only to derive that metric, and
    # by `outbox.claim_job` to write it.
    claimed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Phase 6: "only one ACTIVE job per (content_id, pipeline version)".
        # Active means PENDING or PROCESSING, so a second worker cannot start
        # on the same content while the first is still working, and a new save
        # cannot queue duplicate work for content already in flight.
        Index("processing_jobs_active_uq", "content_id", "job_type",
              unique=True, postgresql_where=text(
                  "status IN ('PENDING', 'PROCESSING')")),
        Index("processing_jobs_dispatch_idx", "status", "available_at"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_RANDOM_UUID)
    item_id = Column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_idx = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    # Phase 11: when this part of the content starts, as written in the source
    # ("14:02"). NULL for content with no timeline: an article has no minute 14.
    start_timestamp = Column(String(16))
    start_seconds = Column(Integer)
    # Phase 12: the chunk's own lexical index. Computed, because chunk_text is a
    # plain immutable text column -- unlike items, which needs JSONB that cannot
    # go inside a STORED generated column.
    tsv = Column(TSVECTOR, Computed(
        "to_tsvector('english', coalesce(chunk_text,''))", persisted=True))
    embedding = Column(Vector(1536), nullable=False)

    __table_args__ = (
        UniqueConstraint("item_id", "chunk_idx", name="chunks_item_idx_uq"),
        Index("chunks_item_idx", "item_id"),
        Index("chunks_tsv_idx", "tsv", postgresql_using="gin"),
        Index("chunks_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )



class Collection(Base):
    __tablename__ = "collections"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("id", "user_id", name="collections_id_user_uq"),
                      Index("collections_user_idx", "user_id"))


class CollectionItem(Base):
    __tablename__ = "collection_items"
    collection_id = Column(UUID(as_uuid=True), primary_key=True)
    item_id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["collection_id", "user_id"], ["collections.id", "collections.user_id"],
                             ondelete="CASCADE", name="collection_items_owner_fk"),
        ForeignKeyConstraint(["item_id", "user_id"], ["items.id", "items.user_id"],
                             ondelete="CASCADE", name="collection_items_item_owner_fk"),
        Index("collection_items_item_idx", "item_id", "user_id"),
    )


class Reminder(Base):
    __tablename__ = 'reminders'
    item_id = Column(UUID(as_uuid=True), ForeignKey('items.id', ondelete='CASCADE'), primary_key=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    time_zone = Column(String(100), nullable=False)
    delivered_at = Column(DateTime(timezone=True))
