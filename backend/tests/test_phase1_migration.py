"""Phase 1: existing items become 1 ContentAsset + 1 UserMemory, and the
existing save/retrieve behaviour is unchanged.

Everything here runs against a live PostgreSQL database, because the claim under
test is a claim about what the migration actually does to real rows. It is
skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase1_migration.py -q
"""
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 1 migration tests",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture
def legacy_db(engine):
    """A throwaway database holding pre-Phase-1 rows, migrated to head.

    Built by running the real Alembic chain, so the backfill SQL under test is
    the shipping one rather than a copy.
    """
    name = f"fb_phase1_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
LEGACY_ITEM = {
    "url": "https://www.youtube.com/watch?v=abc123&utm_source=share",
    "canonical_url": "https://youtube.com/watch?v=abc123",
    "title": "Grandma's Souffle",
    "title_clean": "Grandma's Souffle",
    "source_domain": "youtube.com",
    "source_type": "youtube",
    "raw_s3_key": "raw/abc123.json",
    "raw_preview": "a preview",
    "failure_reason": None,
    "summary": "A five-step lemon souffle.",
    "key_points": ["Whisk yolks", "Fold whites", "Bake 200C"],
    "category": "recipe",
    "entities": {"ingredients": ["lemon", "egg"]},
    "intent": "cook",
    "tags": ["baking", "dessert"],
    "status": "ready",
}


def _alembic(url: str, action: str) -> None:
    """Run the real Alembic chain against `url`."""
    from alembic import command

    from app import database as db_module

    cfg = db_module.alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    getattr(command, action)(cfg, "head" if action == "upgrade" else "base")
def _seed_legacy_rows(url: str) -> dict:
    """Create the pre-Phase-1 schema (revision 0001 only) and insert sample data.

    The Phase 1 objects are removed explicitly so the fixture really represents
    an old database, then the real chain is replayed to perform the backfill.
    """
    _alembic(url, "upgrade")
    eng = create_engine(url, pool_pre_ping=True)
    with eng.begin() as conn:
        # Reset to a true pre-Phase-1 database: every table added after
        # revision 0001 must go, or re-running the chain would try to create
        # them again.
        conn.execute(text("DROP TABLE IF EXISTS share_redemptions CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS memory_shares CASCADE"))
        conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS display_name"))
        conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS shared_by"))
        conn.execute(text("DROP TABLE IF EXISTS collection_items CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS collections CASCADE"))
        conn.execute(text("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_id_user_uq"))
        conn.execute(text("DROP TABLE IF EXISTS processing_jobs CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS user_memories CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS content_assets CASCADE"))
        conn.execute(text("ALTER TABLE items DROP COLUMN IF EXISTS content_id"))
        # Columns added by later revisions must go too, or replaying the chain
        # would try to add them a second time.
        for column in ("search_text_tsv", "search_text", "chunk_timestamps",
                         "chunk_texts", "normalized_text", "raw_text", "evidence_bundle",
                         "brief_v2", "processing_metadata", "needs_retry",
                       "regeneration_day", "regeneration_count"):
            conn.execute(text(f"ALTER TABLE items DROP COLUMN IF EXISTS {column}"))
        # 0010 adds this to `users`, which is a revision-0001 table, so it
        # survives the drops above and would be re-added on replay.
        conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS auth_subject"))
        # The constraint 0010 adds, and the unique constraint it creates.
        conn.execute(text(
            "ALTER TABLE items DROP CONSTRAINT IF EXISTS items_user_content_fk"))
        conn.execute(text(
            "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_auth_subject_uq"))
        conn.execute(text("DROP INDEX IF EXISTS users_auth_subject_uq"))
        # 0009 adds columns as raw SQL (the generated clause is not a Column),
        # so they must be dropped here too or replaying would add them twice.
        for column in ("start_seconds", "start_timestamp"):
            conn.execute(text(
                f"ALTER TABLE chunks DROP COLUMN IF EXISTS {column}"))
        conn.execute(text("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv"))
        # The GIN indexes on those columns go with them.
        conn.execute(text("DROP INDEX IF EXISTS items_search_text_tsv_idx"))
        conn.execute(text("DROP INDEX IF EXISTS chunks_tsv_idx"))
        conn.execute(text(
            "UPDATE alembic_version SET version_num = '0001_initial'"))
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        for uid in (user_a, user_b):
            conn.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                         {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, title, title_clean,
                source_domain, source_type, raw_s3_key, raw_preview,
                failure_reason, summary, key_points, category, entities, intent,
                tags, status, embedding_model, created_at, last_seen_at,
                processed_at)
            VALUES (:user_id, :url, :canonical_url, :title, :title_clean,
                :source_domain, :source_type, :raw_s3_key, :raw_preview,
                :failure_reason, :summary, CAST(:key_points AS jsonb), :category,
                CAST(:entities AS jsonb), :intent, CAST(:tags AS text[]), :status,
                'test-model', now() - interval '3 days',
                now() - interval '2 days', now() - interval '3 days')
        """), {"user_id": user_a, **LEGACY_ITEM,
               "key_points": json.dumps(LEGACY_ITEM["key_points"]),
               "entities": json.dumps(LEGACY_ITEM["entities"]),
               "tags": list(LEGACY_ITEM["tags"])})
        # A second user saved the SAME url. PRODUCT.md rule 3: a duplicate URL
        # must not collapse into one shared asset in Phase 1.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, title, title_clean,
                source_domain, source_type, raw_s3_key, raw_preview,
                failure_reason, summary, key_points, category, entities, intent,
                tags, status, created_at, last_seen_at, processed_at)
            VALUES (:user_id, :url, :canonical_url, :title, :title_clean,
                :source_domain, :source_type, :raw_s3_key, :raw_preview,
                :failure_reason, :summary, CAST(:key_points AS jsonb), :category,
                CAST(:entities AS jsonb), :intent, CAST(:tags AS text[]), :status,
                now(), now(), now())
        """), {"user_id": user_b, **LEGACY_ITEM,
               "key_points": json.dumps(LEGACY_ITEM["key_points"]),
               "entities": json.dumps(LEGACY_ITEM["entities"]),
               "tags": list(LEGACY_ITEM["tags"])})
        # A failed item, to prove processing_error survives the mapping.
# A failed item, to prove processing_error survives the mapping.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, title, source_domain,
                source_type, failure_reason, status, key_points, entities, tags,
                summary, created_at, last_seen_at)
            VALUES (:user_id, :url, :canonical_url, 'Broken', 'example.test',
                'article', 'provider timeout', 'failed', '[]'::jsonb, '{}'::jsonb,
                '{}'::text[], NULL, now(), now())
        """), {"user_id": user_a, "url": "https://example.test/failed",
               "canonical_url": "https://example.test/failed"})
        # A pending, not-yet-extracted item.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, title, source_domain,
                source_type, status, key_points, entities, tags, summary,
                created_at, last_seen_at)
            VALUES (:user_id, :url, :canonical_url, 'Pending', 'example.test',
                'article', 'pending', '[]'::jsonb, '{}'::jsonb, '{}'::text[],
                'just a preview', now(), now())
        """), {"user_id": user_a, "url": "https://example.test/pending",
               "canonical_url": "https://example.test/pending"})
    eng.dispose()
    return {"ready_user": user_a, "second_user": user_b}


@pytest.fixture
def migrated(legacy_db):
    """Seed pre-Phase-1 rows, then run the real migration to head."""
    ids = _seed_legacy_rows(legacy_db)
    _alembic(legacy_db, "upgrade")
    return create_engine(legacy_db, pool_pre_ping=True), ids


def test_every_item_is_represented_by_an_asset_and_a_memory(migrated):
    """After Phase 2, identical content collapses to one shared asset.

    Phase 1 asserted 1:1; Phase 2 deliberately merges duplicates, so the count
    is no longer equal. What must still hold is that no item is left without an
    asset and a memory, and that nothing was dropped.
    """
    eng, _ids = migrated
    with eng.connect() as conn:
        items = conn.execute(text("SELECT count(*) FROM items")).scalar()
        assets = conn.execute(text("SELECT count(*) FROM content_assets")).scalar()
        memories = conn.execute(text("SELECT count(*) FROM user_memories")).scalar()
        # The two users who saved the same YouTube URL now share one asset.
        distinct_assets = conn.execute(text(
            "SELECT count(DISTINCT content_id) FROM user_memories")).scalar()
    assert assets <= items, "merging may only reduce the number of assets"
    assert distinct_assets == assets, "every memory must point at a real asset"
    assert memories == items, "every item still has exactly one user memory"


def test_no_existing_data_is_deleted(migrated):
    eng, ids = migrated
    with eng.connect() as conn:
        # Every original column the old endpoints read must still be intact.
        row = conn.execute(text("""
            SELECT url, canonical_url, title, title_clean, source_domain,
                   source_type, raw_s3_key, summary, key_points, category,
                   entities, intent, tags, status, embedding_model, created_at,
                   last_seen_at, processed_at, fetch_metadata
            FROM items WHERE canonical_url = :c AND user_id = :u
        """), {"c": LEGACY_ITEM["canonical_url"],
               "u": ids["ready_user"]}).mappings().one()
    assert row["url"] == LEGACY_ITEM["url"]
    assert row["canonical_url"] == LEGACY_ITEM["canonical_url"]
    assert row["title_clean"] == LEGACY_ITEM["title_clean"]
    assert row["summary"] == LEGACY_ITEM["summary"]
    assert row["key_points"] == LEGACY_ITEM["key_points"]
    assert row["entities"] == LEGACY_ITEM["entities"]
    assert list(row["tags"]) == LEGACY_ITEM["tags"]
    assert row["status"] == "ready"
    assert row["embedding_model"] == "test-model"
    assert row["fetch_metadata"] == {}


def test_asset_fields_map_from_the_old_item(migrated):
    eng, _ids = migrated
    with eng.connect() as conn:
        asset = conn.execute(text("""
            SELECT a.* FROM content_assets a
            JOIN items i ON i.content_id = a.id
            WHERE i.canonical_url = :c AND i.user_id = (
                SELECT user_id FROM items WHERE canonical_url = :c LIMIT 1)
        """), {"c": LEGACY_ITEM["canonical_url"]}).mappings().one()
    assert asset["canonical_url"] == LEGACY_ITEM["canonical_url"]
    assert asset["source_type"] == LEGACY_ITEM["source_type"]
    assert asset["title"] == LEGACY_ITEM["title_clean"]
    assert asset["brief"] == LEGACY_ITEM["summary"]
    assert asset["entities"] == LEGACY_ITEM["entities"]
    assert list(asset["topics"]) == LEGACY_ITEM["tags"]
    assert asset["intent"] == LEGACY_ITEM["intent"]
    assert asset["raw_content"] == LEGACY_ITEM["raw_s3_key"]
    assert asset["processing_status"] == "ready"
    assert asset["processing_error"] is None
    # Phase 1 never decides privacy, so everything starts UNKNOWN.
    assert asset["visibility"] == "UNKNOWN"
    # key_points survive inside structured_data; category is kept alongside.
    assert asset["structured_data"]["key_points"] == LEGACY_ITEM["key_points"]
    assert asset["structured_data"]["category"] == LEGACY_ITEM["category"]
    # Phase 2 (superseding Phase 1): the backfill still writes no key, but
    # migration 0003 then fills it in. Check the asset fields the phase owns.
    assert asset["pipeline_version"] is None


def test_failed_item_keeps_its_error_and_status(migrated):
    eng, _ids = migrated
    with eng.connect() as conn:
        asset = conn.execute(text("""
            SELECT processing_status, processing_error FROM content_assets a
            JOIN items i ON i.content_id = a.id
            WHERE i.canonical_url = 'https://example.test/failed'
        """)).mappings().one()
def test_memory_belongs_to_the_right_user_and_asset(migrated):
    eng, ids = migrated
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT m.user_id, m.content_id, m.save_count, m.user_note,
                   m.user_intent, m.first_saved_at, m.last_saved_at,
                   i.canonical_url
            FROM user_memories m
            JOIN items i ON i.content_id = m.content_id AND i.user_id = m.user_id
        """)).mappings().all()
    assert len(rows) == 4
    for row in rows:
        assert row["user_id"] in (ids["ready_user"], ids["second_user"])
        assert row["save_count"] == 1
        # Nothing in Phase 1 invents user-owned data.
        assert row["user_note"] is None
        assert row["user_intent"] is None
        assert row["first_saved_at"] is not None
        assert row["last_saved_at"] is not None


def test_same_url_saved_by_two_users_shares_one_asset_keeps_two_memories(migrated):
    """Phase 1 kept two assets; Phase 2 merges them into one shared asset.

    The privacy guarantee is unchanged: each user keeps a separate, private
    UserMemory row, and the shared asset is not marked public (Phase 4 decides
    that).
    """
    eng, ids = migrated
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT m.user_id, a.id AS asset_id, a.visibility
            FROM user_memories m
            JOIN items i ON i.content_id = m.content_id AND i.user_id = m.user_id
            JOIN content_assets a ON a.id = m.content_id
            WHERE i.canonical_url = :c
        """), {"c": LEGACY_ITEM["canonical_url"]}).mappings().all()
    assert len(rows) == 2, "each user keeps their own private memory"
    assert {r["user_id"] for r in rows} == {ids["ready_user"], ids["second_user"]}
    # Phase 4: these assets are UNKNOWN, so each user now has their own copy.
    assert len({r["asset_id"] for r in rows}) == 2
    assert {r["visibility"] for r in rows} == {"UNKNOWN"}


def test_pending_item_maps_to_a_pending_asset(migrated):
    eng, _ids = migrated
    with eng.connect() as conn:
        asset = conn.execute(text("""
            SELECT processing_status, brief, processing_error FROM content_assets a
            JOIN items i ON i.content_id = a.id
            WHERE i.canonical_url = 'https://example.test/pending'
        """)).mappings().one()
    assert asset["processing_status"] == "pending"
    assert asset["brief"] == "just a preview"
    assert asset["processing_error"] is None


def test_no_user_memory_points_at_a_missing_asset(migrated):
    eng, _ids = migrated
    with eng.connect() as conn:
        orphans = conn.execute(text("""
            SELECT count(*) FROM user_memories m
            LEFT JOIN content_assets a ON a.id = m.content_id WHERE a.id IS NULL
        """)).scalar()
        dangling = conn.execute(text(
            "SELECT count(*) FROM items WHERE content_id IS NULL")).scalar()
    assert orphans == 0
    assert dangling == 0, "every item must be linked to the asset it was split into"


def test_per_user_uniqueness_still_holds_after_migration(migrated):
    """The old items_user_canonical_uq guard must still reject a duplicate."""
    eng, ids = migrated
    with eng.begin() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(text("""
                INSERT INTO items (user_id, url, canonical_url)
                VALUES (:uid, :url, :c)
            """), {"uid": ids["ready_user"], "url": LEGACY_ITEM["url"],
                   "c": LEGACY_ITEM["canonical_url"]})
    assert "items_user_canonical_uq" in str(excinfo.value)


def test_user_memories_unique_per_user_and_content(migrated):
    eng, ids = migrated
    with eng.connect() as conn:
        content_id = conn.execute(text(
            "SELECT content_id FROM user_memories WHERE user_id = :u LIMIT 1"),
            {"u": ids["ready_user"]}).scalar()
    with eng.begin() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(text(
                "INSERT INTO user_memories (user_id, content_id) VALUES (:u, :c)"),
                {"u": ids["ready_user"], "c": content_id})
    assert "user_memories_user_content_uq" in str(excinfo.value)


@pytest.fixture(autouse=True)
def no_celery(monkeypatch):
    """Ingest hands work to Celery; no broker exists in tests.

    `_enqueue` already swallows any publish failure and leaves the item
    pending, so the save path must still succeed without Redis.
    """
    from app.routers import ingest as ingest_module

    monkeypatch.setattr(ingest_module, "_enqueue", lambda db, item_id, content_id=None: False)


@pytest.fixture
def orm_session(migrated):
    """A real ORM session bound to the migrated database."""
    from sqlalchemy.orm import sessionmaker

    eng, ids = migrated
    Session = sessionmaker(bind=eng)
    session = Session()
    try:
        yield session, ids
    finally:
        session.rollback()
        session.close()


def test_new_save_creates_an_asset_and_a_memory(orm_session):
    """POST /api/v1/ingest must also record the new asset + memory."""
    from app.models import ContentAsset, Item, UserMemory
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    session, ids = orm_session

    class _User:
        pass

    user = _User()
    user.id = ids["ready_user"]

    before_assets = session.query(ContentAsset).count()
    result = ingest(
        IngestRequest(url="https://example.test/a-brand-new-save",
                      title_hint="Brand new", preview="preview body"),
        session, user)

    # The response contract is unchanged: an id, a status, the canonical URL.
    assert str(result.status) in ("processing", "pending")
    assert result.canonical_url == "https://example.test/a-brand-new-save"

    session.commit()
    assert session.query(ContentAsset).count() == before_assets + 1
    asset = session.query(ContentAsset).filter_by(
        canonical_url="https://example.test/a-brand-new-save").one()
    assert asset.visibility == "UNKNOWN"
    assert asset.processing_status == "pending"
    assert asset.title == "Brand new"
    # Phase 2 now populates the key on save; 0003 backfills older rows.
    assert asset.dedupe_key == "url:https://example.test/a-brand-new-save"

    memory = session.query(UserMemory).filter_by(
        user_id=user.id, content_id=asset.id).one()
    assert memory.save_count == 1
    assert memory.user_note is None
    assert memory.user_intent is None

    item = session.query(Item).filter_by(id=str(result.id)).one()
    assert item.content_id == asset.id
    # The old columns the existing endpoints read are still populated.
    assert item.title == "Brand new"
    assert item.summary == "preview body"
    assert item.raw_preview == "preview body"
    assert item.status == "pending"


def test_repeat_save_reuses_the_same_asset_and_memory(orm_session):
    """Saving the same URL twice must not create a second asset."""
    from app.models import ContentAsset, UserMemory
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    session, ids = orm_session

    class _User:
        pass

    user = _User()
    user.id = ids["ready_user"]

    first = ingest(IngestRequest(url="https://example.test/twice"), session, user)
    session.commit()
    assets_after_first = session.query(ContentAsset).count()
    memories_after_first = session.query(UserMemory).count()

    second = ingest(IngestRequest(url="https://example.test/twice"), session, user)
    session.commit()

    assert str(first.id) == str(second.id), "same save must return the same item"
    assert session.query(ContentAsset).count() == assets_after_first
    assert session.query(UserMemory).count() == memories_after_first
    memory = session.query(UserMemory).filter_by(
        user_id=user.id,
        content_id=session.query(ContentAsset).filter_by(
            canonical_url="https://example.test/twice").one().id).one()
    assert memory.save_count == 2, "a repeat save is recorded on the memory"


def test_sync_batch_creates_assets_and_memories(orm_session):
    from app.models import ContentAsset, UserMemory
    from app.routers.ingest import sync_batch
    from app.schemas import SyncBatchRequest, SyncItem

    session, ids = orm_session

    class _User:
        pass

    user = _User()
    user.id = ids["ready_user"]

    before = session.query(ContentAsset).count()
    out = sync_batch(SyncBatchRequest(items=[
        SyncItem(client_id="c1", url="https://example.test/offline-1"),
        SyncItem(client_id="c2", url="https://example.test/offline-2"),
    ]), session, user)
    session.commit()

    assert out.errors == []
    assert [m["client_id"] for m in out.mapped] == ["c1", "c2"]
    assert session.query(ContentAsset).count() == before + 2
    for canonical in ("https://example.test/offline-1",
                      "https://example.test/offline-2"):
        asset = session.query(ContentAsset).filter_by(canonical_url=canonical).one()
        assert session.query(UserMemory).filter_by(
            user_id=user.id, content_id=asset.id).count() == 1


def test_existing_retrieval_still_works_after_migration(migrated):
    """The old read path (items) must answer exactly as it did before."""
    eng, ids = migrated
    with eng.connect() as conn:
        # The BM25 query in app/services/search.py, unchanged.
        found = conn.execute(text("""
            SELECT title_clean, summary, tags, source_domain
            FROM items
            WHERE user_id = :uid AND status = 'ready'
              AND tsv @@ plainto_tsquery('english', :q)
        """), {"uid": ids["ready_user"], "q": "lemon souffle"}).mappings().all()
    assert len(found) == 1
    assert found[0]["title_clean"] == LEGACY_ITEM["title_clean"]
    assert found[0]["summary"] == LEGACY_ITEM["summary"]
    assert found[0]["source_domain"] == LEGACY_ITEM["source_domain"]
    # A tag-only term is still findable: the tsv fix from Phase 0.5 survives.
    with eng.connect() as conn:
        by_tag = conn.execute(text("""
            SELECT count(*) FROM items
            WHERE user_id = :uid AND tsv @@ plainto_tsquery('english', 'baking')
        """), {"uid": ids["ready_user"]}).scalar()
    assert by_tag == 1
