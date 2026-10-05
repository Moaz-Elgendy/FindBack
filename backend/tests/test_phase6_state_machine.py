"""Phase 6: the processing state machine and idempotency.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase6_state_machine.py -q
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
    reason="TEST_DATABASE_URL not set; skipping Phase 6 state-machine tests",
)

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SAVES = 10


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
    name = f"fb_phase6_{uuid.uuid4().hex[:10]}"
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
def live_queue(monkeypatch):
    """A healthy queue: `delay` succeeds and records what was published."""
    from app.tasks import process_item

    published = []
    monkeypatch.setattr(process_item, "delay", published.append)
    return published


class _UserRow:
    def __init__(self, uid):
        self.id = uid


def _make_users(sessions, count):
    ids = [uuid.uuid4() for _ in range(count)]
    with sessions() as s:
        for index, uid in enumerate(ids):
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"u{index}@example.test"})
        s.commit()
    return ids


def _save(session, user_id, url=VIDEO):
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint="Video",
                                  preview="preview body"),
                    session, _UserRow(user_id))
    session.commit()
    return result


def _publish_public(sessions):
    """Make the content shareable, which is what lets N users meet one asset."""
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET visibility='PUBLIC', "
                       "owner_user_id=NULL"))
        s.commit()
# --- REQUIRED concurrency test -------------------------------------------

def test_ten_concurrent_saves_of_the_same_content(db, sessions, live_queue):
    """10 concurrent saves of the same content must give exactly:
    1 ContentAsset, 1 active job, 1 processing operation, 10 UserMemory rows.

    The content is PUBLIC, so all ten users legitimately share one asset
    (Phase 4). Ten UNKNOWN saves would instead be ten private assets, which is
    the correct result for unshared content and is covered in Phase 4.
    """
    users = _make_users(sessions, SAVES)
    with sessions() as s:
        _save(s, users[0])
    _publish_public(sessions)

    barrier = threading.Barrier(SAVES)
    errors = []
    lock = threading.Lock()

    def worker(user_id):
        try:
            with sessions() as session:
                barrier.wait(timeout=30)
                _save(session, user_id)
        except Exception as exc:  # noqa: BLE001 - reported below
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(u,)) for u in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)

    assert not errors, f"concurrent saves raised: {errors}"

    with sessions() as s:
        assets = s.execute(text("SELECT count(*) FROM content_assets")).scalar()
        active_jobs = s.execute(text(
            "SELECT count(*) FROM processing_jobs "
            "WHERE status IN ('PENDING', 'PROCESSING')")).scalar()
        memories = s.execute(text(
            "SELECT count(*) FROM user_memories")).scalar()
        distinct = s.execute(text(
            "SELECT count(DISTINCT content_id) FROM user_memories")).scalar()

    assert assets == 1, f"expected 1 ContentAsset, got {assets}"
    assert active_jobs == 1, f"expected 1 active job, got {active_jobs}"
    assert memories == SAVES, f"expected {SAVES} UserMemory rows, got {memories}"
    assert distinct == 1, "all memories must reference the one asset"

    # Exactly one processing operation happens, even though every item is
    # handed to a worker: only the first claim gets through.
    processed = _process_all(db, sessions)
    assert processed == 1, f"expected 1 processing operation, got {processed}"

    with sessions() as s:
        jobs = s.execute(text(
            "SELECT status, attempt_count FROM processing_jobs")).mappings().all()
    assert [j["status"] for j in jobs] == ["READY"]
    assert jobs[0]["attempt_count"] == 1


def _process_all(db, sessions):
    """Run the real worker for every item; count real processing operations."""
    import app.tasks as app_tasks
    from app import database as db_module
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    calls = []
    lock = threading.Lock()

    async def _fetch(url, preview=""):
        with lock:
            calls.append(url)
        return {"text": "chicken cream mushroom risotto", "title": "Risotto",
                "source_type": "article"}

    async def _extract(raw_text, url_title="", url=""):
        return Brief(title="Risotto", overview="A risotto", highlights=["one"],
                     topics=["dinner"], intent=["cook"],
                     structured_data={"content_type": "recipe"})

    async def _embed_many(texts, task="document"):
        return [[0.0] * 1536 for _ in texts]

    fetcher.fetch_content = _fetch
    extractor.extract_brief = _extract
    storage.store_raw_snapshot = lambda item_id, payload: None
    embedder.chunk_text_with_timestamps = (
        lambda text, size=800, overlap=160: [{"chunk_idx": 0, "chunk_text": text,
                                            "start_timestamp": None,
                                            "start_seconds": None}])
    embedder.embed_many = _embed_many
    embedder.embedding_model_name = lambda: "test-model"

    with sessions() as s:
        item_ids = [row[0] for row in s.execute(text(
            "SELECT id FROM items ORDER BY created_at")).fetchall()]

    previous_engine = db_module._engine
    previous_factory = db_module._sessionmaker
    db_module._engine = db
    # get_sessionmaker() memoises its factory, so it must be reset too or a
    # later test keeps using a session bound to a dropped database.
    db_module._sessionmaker = None
    try:
        for item_id in item_ids:
            app_tasks.process_item.apply(args=(str(item_id),), throw=True)
    finally:
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_factory
    return len(calls)


# --- idempotency ---------------------------------------------------------

def _counts(sessions):
    with sessions() as s:
        return {
            "assets": s.execute(text("SELECT count(*) FROM content_assets")).scalar(),
            "chunks": s.execute(text("SELECT count(*) FROM chunks")).scalar(),
            "jobs": s.execute(text("SELECT count(*) FROM processing_jobs")).scalar(),
            "items": s.execute(text("SELECT count(*) FROM items")).scalar(),
            "embeddings": s.execute(text(
                "SELECT count(*) FROM items WHERE embedding IS NOT NULL")).scalar(),
        }


def test_running_the_same_job_twice_creates_no_duplicates(
        db, sessions, live_queue):
    """Re-running a finished job must be a complete no-op."""
    users = _make_users(sessions, 2)
    # User 0 saves, the content is published, then user 1 saves and joins it.
    # Publishing after both saves would be invalid: while the content is
    # UNKNOWN each user has their own private asset (Phase 4).
    with sessions() as s:
        _save(s, users[0])
    _publish_public(sessions)
    with sessions() as s:
        _save(s, users[1])

    first = _process_all(db, sessions)
    after_first = _counts(sessions)
    second = _process_all(db, sessions)
    after_second = _counts(sessions)

    assert first >= 1
    # The second pass did no real work at all.
    assert second == 0, "a READY job must not be processed again"
    assert after_first == after_second, f"{after_first} -> {after_second}"


def test_reprocessing_a_single_item_is_idempotent(db, sessions, live_queue):
    """Force the job back to PENDING and re-run: no duplicate chunks/rows."""
    users = _make_users(sessions, 1)
    with sessions() as s:
        item_id = str(_save(s, users[0]).id)
    _process_all(db, sessions)
    first = _counts(sessions)
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET status='PENDING'"))
        s.commit()
    _process_all(db, sessions)
    second = _counts(sessions)
    assert first == second, f"re-processing duplicated something: {first} -> {second}"


# --- the state machine itself --------------------------------------------

def test_only_the_four_allowed_states_exist(db):
    from app.models import JOB_STATUSES

    assert set(JOB_STATUSES) == {"PENDING", "PROCESSING", "READY", "FAILED"}
    with db.connect() as conn:
        distinct = conn.execute(text(
            "SELECT DISTINCT status FROM processing_jobs")).scalars().all()
    assert set(distinct) <= set(JOB_STATUSES), \
        f"unexpected states in the database: {distinct}"


def test_failure_updates_attempt_count_last_error_and_status(
        db, sessions, live_queue, monkeypatch, successful_brief):
    """A failing worker records the attempt and the reason."""
    import app.tasks as app_tasks

    users = _make_users(sessions, 1)
    with sessions() as s:
        _save(s, users[0])

    async def _boom(texts, task="document"):
        raise RuntimeError("provider exploded")

    from app.services import embedder
    monkeypatch.setattr(embedder, "embed_many", _boom)
    with db.connect() as conn:
        item_id = conn.execute(text("SELECT id FROM items")).scalar()

    previous = db_module_engine(db)
    try:
        app_tasks.process_item.apply(args=(str(item_id),), throw=False)
    finally:
        previous()

    with sessions() as s:
        job = s.execute(text(
            "SELECT status, attempt_count, last_error FROM processing_jobs")
        ).mappings().one()
    assert job["attempt_count"] >= 1, "a retry must be counted"
    assert job["last_error"] and "exploded" in job["last_error"]
    assert job["status"] in ("PENDING", "FAILED")


def db_module_engine(db):
    from app import database as db_module

    previous = db_module._engine
    previous_factory = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = None

    def restore():
        db_module._engine = previous
        db_module._sessionmaker = previous_factory

    return restore
