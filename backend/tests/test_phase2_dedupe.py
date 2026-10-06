"""Phase 2: the same content resolves to one ContentAsset, and the database
guarantees it.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase2_dedupe.py -q
"""
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 2 dedupe tests",
)

# One canonical piece of content, reachable by many URL shapes.
VIDEO_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&utm_source=share",
]


@pytest.fixture(scope="module")
def admin_engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture
def db(admin_engine):
    """A freshly migrated, empty database."""
    name = f"fb_phase2_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = create_engine(url, pool_pre_ping=True)
    try:
        from alembic import command

        from app import database as db_module

        cfg = db_module.alembic_config()
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def sessions(db):
    return sessionmaker(bind=db)


@pytest.fixture(autouse=True)
def no_celery(monkeypatch):
    from app.routers import ingest as ingest_module

    monkeypatch.setattr(ingest_module, "_enqueue", lambda db, item_id, content_id=None: False)


@pytest.fixture
def users(db, sessions):
    ids = [uuid.uuid4() for _ in range(3)]
    with sessions() as s:
        for uid in ids:
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        s.commit()
    return ids


class _User:
    def __init__(self, uid):
        self.id = uid


def _save(session, user_id, url, title=None, preview=None):
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint=title, preview=preview),
                    session, _User(user_id))
    session.commit()
    return result


def _counts(session):
    from sqlalchemy import text as _text

    assets = session.execute(_text("SELECT count(*) FROM content_assets")).scalar()
    memories = session.execute(_text("SELECT count(*) FROM user_memories")).scalar()
    return assets, memories


# --- required test 1: same content, same user ----------------------------

def test_same_content_same_user_is_one_asset_and_one_memory(db, sessions, users):
    user = users[0]
    with sessions() as s:
        first = _save(s, user, VIDEO_URLS[0], title="Rick Astley")
        second = _save(s, user, "https://youtu.be/dQw4w9WgXcQ")
        assets, memories = _counts(s)
    assert str(first.id) == str(second.id), "one user, one item"
    assert first.already_exists is False
    assert second.already_exists is True
    assert assets == 1
    assert memories == 1
    with sessions() as s:
        row = s.execute(text(
            "SELECT dedupe_key FROM content_assets")).scalar()
    assert row == "youtube:dQw4w9WgXcQ"


# --- required test 2: same content, different users ----------------------

def _publish(session, canonical_url=None) -> None:
    """Mark assets PUBLIC, which is what Phase 4 requires before sharing."""
    if canonical_url:
        session.execute(text(
            "UPDATE content_assets SET visibility='PUBLIC', owner_user_id=NULL "
            "WHERE canonical_url = :c"), {"c": canonical_url})
    else:
        session.execute(text(
            "UPDATE content_assets SET visibility='PUBLIC', owner_user_id=NULL"))
    session.commit()


# --- required test 2: same content, different users ----------------------
# Phase 4 supersedes the original Phase 2 expectation: cross-user sharing now
# requires the content to be explicitly PUBLIC. UNKNOWN is private, so the
# first save is published before the others join it.

def test_public_content_is_shared_across_users(db, sessions, users):
    with sessions() as s:
        _save(s, users[0], VIDEO_URLS[0], title="Rick Astley")
        _publish(s)
        for user in users[1:]:
            with sessions() as s2:
                _save(s2, user, VIDEO_URLS[0], title="Rick Astley")
    with sessions() as s:
        assets, memories = _counts(s)
        rows = s.execute(text(
            "SELECT user_id, content_id FROM user_memories ORDER BY user_id")
        ).mappings().all()
    assert assets == 1, "PUBLIC content must be shared by every user"
    assert memories == 3, "each user keeps their own memory"
    assert len({r["user_id"] for r in rows}) == 3
    assert len({r["content_id"] for r in rows}) == 1


# --- required test 3: different URL forms of the same content ------------

def test_every_url_form_resolves_to_one_asset(db, sessions, users):
    with sessions() as s:
        _save(s, users[0], VIDEO_URLS[0], title="Rick Astley")
        _publish(s)
        for index, url in enumerate(VIDEO_URLS):
            with sessions() as s2:
                _save(s2, users[index % len(users)], url, title="Rick Astley")
    with sessions() as s:
        assets, memories = _counts(s)
    assert assets == 1, f"watch/youtu.be/embed/shorts must be one asset, got {assets}"
    assert memories == 3


def test_a_different_video_is_a_different_asset(db, sessions, users):
    with sessions() as s:
        _save(s, users[0], "https://youtu.be/dQw4w9WgXcQ")
        _save(s, users[1], "https://youtu.be/aaaaaaaaaaa")
    with sessions() as s:
        assets, _ = _counts(s)
    assert assets == 2


def test_different_pages_are_different_assets(db, sessions, users):
    with sessions() as s:
        _save(s, users[0], "https://example.com/a")
        _save(s, users[1], "https://example.com/b")
        # Same page, different tracking noise: still one asset for that user.
        _save(s, users[2], "https://example.com/a?utm_source=twitter")
        assets, memories = _counts(s)
    assert assets == 3, "UNKNOWN content is private: one asset per user"
    assert memories == 3


# --- required test 4: concurrent duplicate creation ----------------------

def test_database_constraint_rejects_a_duplicate_key(db, sessions, users):
    """The DB must refuse a second asset for the same owner and key."""
    with sessions() as s:
        _save(s, users[0], VIDEO_URLS[0])
    with sessions() as s:
        # The violation is raised by execute(), not commit(), because the
        # statement is sent immediately.
        with pytest.raises(Exception) as excinfo:
            s.execute(text(
                "INSERT INTO content_assets (canonical_url, dedupe_key, "
                "visibility, owner_user_id) VALUES "
                "('https://elsewhere.test/x', 'youtube:dQw4w9WgXcQ', "
                "'UNKNOWN', :u)"), {"u": users[0]})
        s.rollback()
    assert "content_assets_owner_dedupe_uq" in str(excinfo.value)


def test_concurrent_saves_of_the_same_content_create_one_asset(db, sessions, users):
    """Threads racing on the same content must converge on one asset.

    Each thread uses its own session and connection, so this exercises a real
    concurrent insert rather than a simulated one. The UNIQUE constraint is the
    only thing serialising them: a SELECT-then-INSERT would let some through.
    """
    thread_count = 6
    barrier = threading.Barrier(thread_count)
    errors = []
    item_ids = []
    lock = threading.Lock()

    def worker(index):
        try:
            # Own session/connection per thread.
            with sessions() as session:
                # Release all threads at the same instant to maximise overlap.
                barrier.wait(timeout=30)
                result = _save(session, users[index % len(users)],
                               VIDEO_URLS[index % len(VIDEO_URLS)],
                               title="Rick Astley")
                with lock:
                    item_ids.append(str(result.id))
        except Exception as exc:  # noqa: BLE001 - reported below
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"concurrent saves raised: {errors}"
    with sessions() as s:
        assets, memories = _counts(s)
        distinct_assets = s.execute(text(
            "SELECT count(DISTINCT content_id) FROM user_memories")).scalar()
    # Phase 4: UNKNOWN content is private, so racing saves by 3 users produce
    # one private asset each -- still exactly one per user, never a duplicate.
    assert assets == 3, f"racing inserts must not create extra assets, got {assets}"
    assert memories == 3, "one memory per (user, asset); 6 threads over 3 users"
    assert distinct_assets == 3


def test_concurrent_public_saves_still_converge_on_one_asset(db, sessions, users):
    """The shared path keeps its guarantee: PUBLIC content is still one asset."""
    with sessions() as s:
        _save(s, users[0], VIDEO_URLS[0])
        _publish(s)
    barrier = threading.Barrier(6)
    errors = []

    def worker(index):
        try:
            with sessions() as session:
                barrier.wait(timeout=30)
                _save(session, users[index % len(users)], VIDEO_URLS[0])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, f"concurrent saves raised: {errors}"
    with sessions() as s:
        assets, memories = _counts(s)
        distinct = s.execute(text(
            "SELECT count(DISTINCT content_id) FROM user_memories")).scalar()
    assert assets == 1, f"PUBLIC racing inserts must converge, got {assets}"
    assert memories == 3
    assert distinct == 1


def test_concurrent_saves_by_different_users_keep_every_memory(db, sessions, users):
    barrier = threading.Barrier(3)
    errors = []

    def worker(user):
        try:
            with sessions() as session:
                barrier.wait(timeout=30)
                _save(session, user, VIDEO_URLS[0])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(u,)) for u in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"concurrent saves raised: {errors}"
    with sessions() as s:
        assets, memories = _counts(s)
    assert assets == 3, "UNKNOWN content stays private: one asset per user"
    assert memories == 3, "no user's memory may be lost to a race"


def test_migration_merges_duplicate_assets_from_phase1(db, sessions):
    """Phase 1 made one asset per save; Phase 2 must merge them, keeping data.

    Simulates the real upgrade: rows are written at revision 0002 (no dedupe
    key, duplicates present), then 0003 runs.
    """
    from alembic import command

    from app import database as db_module

    # db.url masks the password, so build the URL from the environment value.
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + db.url.database
    cfg = db_module.alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)

    # Rewind to just before 0003 and insert duplicates the Phase 1 way.
    command.downgrade(cfg, "0002_content_asset_user_memory")
    users = [uuid.uuid4(), uuid.uuid4()]
    with sessions() as s:
        for uid in users:
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        for index, uid in enumerate(users):
            s.execute(text("""
                INSERT INTO items (user_id, url, canonical_url, title,
                    title_clean, source_domain, source_type, summary, key_points,
                    entities, tags, status, created_at, last_seen_at)
                VALUES (:u, :url, :c, 'Video', 'Video', 'youtube.com', 'youtube',
                    'A summary', '[]'::jsonb, '{}'::jsonb, '{}'::text[],
                    'ready', now(), now())
            """), {"u": uid, "url": VIDEO_URLS[index],
                   "c": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        s.commit()
        # Run the Phase 1 backfill by hand so duplicates exist exactly as it
        # would have left them: one asset per item.
        s.execute(text("""
            INSERT INTO content_assets (id, canonical_url, source_type,
                visibility, processing_status, title, brief, created_at, updated_at)
            SELECT i.id, i.canonical_url, i.source_type, 'unknown', i.status,
                   i.title_clean, i.summary, i.created_at, i.created_at
            FROM items i
        """))
        s.execute(text("""
            INSERT INTO user_memories (id, user_id, content_id, first_saved_at,
                last_saved_at, save_count, created_at, updated_at)
            SELECT i.id, i.user_id, i.id, i.created_at, i.last_seen_at, 1,
                   i.created_at, i.created_at
            FROM items i
        """))
        s.execute(text("UPDATE items SET content_id = items.id"))
        s.commit()
        before = s.execute(text("SELECT count(*) FROM content_assets")).scalar()
        assert before == 2, "precondition: Phase 1 left two duplicate assets"

    # Phase 2 merges; Phase 4 then splits the (now UNKNOWN) asset back per
    # user, because unclassified content must not stay shared. Both steps are
    # asserted so neither migration is only half-proven.
    command.upgrade(cfg, "0003_content_dedupe")
    with sessions() as s:
        assert s.execute(text("SELECT count(*) FROM content_assets")).scalar() == 1, \
            "0003 must merge the duplicate assets"
        assert s.execute(text("SELECT count(*) FROM user_memories")).scalar() == 2

    command.upgrade(cfg, "head")
    with sessions() as s:
        assets = s.execute(text("SELECT count(*) FROM content_assets")).scalar()
        memories = s.execute(text("SELECT count(*) FROM user_memories")).scalar()
        distinct = s.execute(text(
            "SELECT count(DISTINCT content_id) FROM user_memories")).scalar()
        keys = s.execute(text(
            "SELECT dedupe_key FROM content_assets")).scalars().all()
        owners = s.execute(text(
            "SELECT visibility, owner_user_id FROM content_assets")).all()
    # 0004 must un-share the UNKNOWN asset: one private copy per user.
    assert assets == 2, "UNKNOWN content must not stay shared between users"
    assert memories == 2, "both users' memories must survive"
    assert distinct == 2
    assert set(keys) == {"youtube:dQw4w9WgXcQ"}
    assert all(v == "UNKNOWN" and o is not None for v, o in owners)


def test_null_dedupe_keys_do_not_collide(db, sessions, users):
    """PostgreSQL treats NULLs as distinct, so unidentified content is safe."""
    with sessions() as s:
        s.execute(text(
            "INSERT INTO content_assets (canonical_url, dedupe_key) "
            "VALUES ('https://a.test/1', NULL), ('https://a.test/2', NULL)"))
        s.commit()
        assets, _ = _counts(s)
    assert assets == 2