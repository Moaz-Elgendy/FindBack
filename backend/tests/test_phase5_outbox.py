"""Phase 5: a temporary queue outage never leaves a save stuck.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase5_outbox.py -q
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 5 outbox tests",
)

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class QueueDown(RuntimeError):
    """Stands in for Redis/Celery being unreachable."""


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
    name = f"fb_phase5_{uuid.uuid4().hex[:10]}"
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


@pytest.fixture
def user(db, sessions):
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        s.commit()
    return uid


@pytest.fixture
def dead_queue(monkeypatch):
    """Make the Celery publish fail, the way a Redis outage does.

    Only `delay` is replaced: the rest of `_enqueue` (including its job
    bookkeeping) still runs, so this exercises the real code path.
    """
    from app.tasks import process_item

    def _raise(item_id):
        raise QueueDown("redis unavailable")

    monkeypatch.setattr(process_item, "delay", _raise)
    return process_item


@pytest.fixture
def live_queue(monkeypatch):
    """A healthy queue: `delay` succeeds and records what was published."""
    from app.tasks import process_item

    published = []
    monkeypatch.setattr(process_item, "delay", published.append)
    return published


from app.models import JOB_TYPE_PROCESS
from app.services.outbox import dispatch_once, record_job


class _UserRow:
    def __init__(self, uid):
        self.id = uid


def _save(session, user_id, url=VIDEO):
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint="Video",
                                  preview="preview body"),
                    session, _UserRow(user_id))
    session.commit()
    return result


def _jobs(session):
    return session.execute(text(
        "SELECT id, content_id, job_type, status, attempt_count, available_at, "
        "locked_at, last_error FROM processing_jobs ORDER BY created_at")
    ).mappings().all()


def _run_worker(session, item_id):
    """Run the real Celery task body against the test database.

    Only the outside world is faked (fetch, model, storage, embeddings); the
    task's own logic and the SQL it issues are the real ones.
    """
    from app import tasks as app_tasks
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    async def _fetch(url, preview=""):
        return {"text": "chicken cream mushroom risotto", "title": "Risotto",
                "source_type": "article"}

    async def _extract(raw_text, url_title="", url=""):
        return Brief(title="Risotto", overview="A risotto", highlights=["one"],
                     topics=["dinner"], intent=["cook"],
                     structured_data={"content_type": "recipe"})

    async def _embed_many(texts, task="document"):
        return [[0.0] * 1536 for _ in texts]

    fetcher.fetch_content = _fetch
    fetcher.fetch_content = _fetch
    extractor.extract_brief = _extract
    storage.store_raw_snapshot = lambda item_id, payload: None
    embedder.chunk_text_with_timestamps = (
        lambda text, size=800, overlap=160: [{"chunk_idx": 0, "chunk_text": text,
                                            "start_timestamp": None,
                                            "start_seconds": None}])
    embedder.embed_many = _embed_many
    embedder.embed_text = lambda text, task="document": _embed_many([text])[0]
    embedder.embedding_model_name = lambda: "test-model"
    # `apply()` runs the task eagerly, in-process, with no broker -- the same
    # body a real Celery worker executes.
    from app import database as db_module

    # The task opens its own session from the module-level engine, which is
    # bound to DATABASE_URL rather than the throwaway test database. Point it
    # at the test engine so the worker's own SQL really lands here.
    previous_engine = db_module._engine
    db_module._engine = session.get_bind()
    try:
        app_tasks.process_item.apply(args=(item_id,), throw=True)
    finally:
        db_module._engine = previous_engine
    session.commit()


# --- the required scenario, step by step ---------------------------------

def test_outage_does_not_stuck_a_save(db, sessions, user, dead_queue, monkeypatch):
    """1) DB write succeeds  2) publish fails  3) queue recovers
    4) dispatcher runs -> the content gets processed.

    Without the outbox the save from step 1 would sit `pending` forever after
    step 2.
    """
    # 1. The save itself succeeds: Redis is down, the database is not.
    with sessions() as s:
        result = _save(s, user)
        item = s.execute(text("SELECT id, status FROM items WHERE id = :i"),
                         {"i": str(result.id)}).mappings().one()
        jobs = _jobs(s)
        assets = s.execute(text("SELECT count(*) FROM content_assets")).scalar()
    assert item["status"] == "pending"
    assert result.status == "pending", "the API reports the truth: not queued"
    assert assets == 1
    assert len(jobs) == 1, "the processing intent must be durable"
    assert jobs[0]["status"] == "PENDING"
    assert jobs[0]["attempt_count"] == 0

    # 2. A dispatcher tick while the queue is still down must not lose it.
    with sessions() as s:
        def failing_publisher(item_id):
            raise QueueDown("redis still down")

        stats = dispatch_once(s, publisher=failing_publisher)
        job = _jobs(s)[0]
    assert stats["failed"] == 1
    assert job["status"] == "PENDING", "a failed publish must stay pending"
    assert job["attempt_count"] == 1
    assert job["last_error"] and "QueueDown" in job["last_error"]
    assert job["locked_at"] is None, "a failed job must not stay locked"

    # 3./4. The queue recovers; the dispatcher publishes the stranded work.
    published = []
    monkeypatch.setattr(dead_queue, "delay", published.append)
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET available_at = now()"))
        s.commit()
        stats = dispatch_once(s, publisher=published.append)
        job = _jobs(s)[0]
        item = s.execute(text("SELECT id, status FROM items WHERE id = :i"),
                         {"i": str(result.id)}).mappings().one()
    assert stats["published"] == 1
    assert job["status"] == "PROCESSING"
    assert str(item["id"]) in published, "the pending item was published"
    assert item["status"] == "pending", "still pending until the worker runs"

    # The worker runs the existing Celery task and finishes the job.
    with sessions() as s:
        _run_worker(s, str(item["id"]))
        processed = s.execute(text("SELECT status FROM items WHERE id = :i"),
                              {"i": str(item["id"])}).scalar()
    assert processed == "ready", "content got processed after the outage"


def test_only_one_active_job_per_content_and_pipeline(
        db, sessions, user, live_queue):
    """The partial unique index is the authority on 'one active job'.

    Phase 6 widened "active" to PENDING *or* PROCESSING, so a job that is
    already being worked on still blocks a duplicate.
    """
    active = ("SELECT count(*) FROM processing_jobs "
              "WHERE status IN ('PENDING', 'PROCESSING')")

    with sessions() as s:
        _save(s, user)
        content_id = s.execute(text("SELECT content_id FROM items")).scalar()
        record_job(s, content_id, JOB_TYPE_PROCESS)
        s.commit()
        assert s.execute(text(active)).scalar() == 1, \
            "recording again must not create a second job"

    # A second worker may not start while the job is in flight: recording
    # again is silently absorbed by ON CONFLICT DO NOTHING, so the invariant to
    # assert is that the job COUNT does not grow.
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET status='PROCESSING', "
                       "locked_at=now()"))
        s.commit()
        before = s.execute(text(
            "SELECT count(*) FROM processing_jobs")).scalar()
        record_job(s, content_id, JOB_TYPE_PROCESS)
        s.commit()
        after = s.execute(text(
            "SELECT count(*) FROM processing_jobs")).scalar()
    assert after == before, "an in-flight job must block a duplicate"

    # A different pipeline version is a different job.
    with sessions() as s:
        record_job(s, content_id, "process_item:v2")
        s.commit()
        total = s.execute(text("SELECT count(*) FROM processing_jobs")).scalar()
    assert total == 2, "a different pipeline version is a different job"

    # Once terminal, a fresh job for the same pipeline is allowed again.
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET status='READY'"))
        s.commit()
        record_job(s, content_id, JOB_TYPE_PROCESS)
        s.commit()
        assert s.execute(text(active)).scalar() == 1, \
            "a finished job no longer blocks a new one"


def test_sync_batch_also_records_durable_jobs(db, sessions, user, live_queue):
    from app.routers.ingest import sync_batch
    from app.schemas import SyncBatchRequest, SyncItem

    with sessions() as s:
        out = sync_batch(SyncBatchRequest(items=[
            SyncItem(client_id="a", url="https://example.com/one"),
            SyncItem(client_id="b", url="https://example.com/two"),
        ]), s, _UserRow(user))
        s.commit()
        assert out.errors == []
        jobs = _jobs(s)
    assert len(jobs) == 2
    # With a healthy queue the fast path publishes and closes the job out.
    # Either closed or still pending is safe; what must NOT happen is a job
    # that is neither (e.g. stuck in a locked state).
    assert all(j["status"] in ("PROCESSING", "PENDING") for j in jobs)
    assert live_queue, "a healthy queue must have received the work"


def test_dispatcher_publishes_every_item_of_shared_content(
        db, sessions, user, live_queue):
    """One job, one publish per item: a PUBLIC asset can back several items."""
    other = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": other, "e": f"{other.hex[:8]}@example.test"})
        s.commit()
        first = _save(s, user)
        s.execute(text(
            "UPDATE content_assets SET visibility='PUBLIC', owner_user_id=NULL"))
        s.commit()
    with sessions() as s:
        second = _save(s, other)
        assert str(first.id) != str(second.id), "two users, two items"
        # Simulate a queue outage: the jobs are still unpublished PENDING.
        # (Phase 6: an already-published job is PROCESSING and must NOT be
        # republished by the dispatcher.)
        s.execute(text("UPDATE processing_jobs SET status='PENDING', "
                       "locked_at=NULL, available_at=now()"))
        s.commit()
        published = []
        stats = dispatch_once(s, publisher=published.append)
    assert stats["published"] == 1
    assert str(first.id) in published and str(second.id) in published, \
        "both pending items must be published"
