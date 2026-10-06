"""A worker that dies mid-job must not strand the save.

Before: `claim_batch` only reclaimed a PROCESSING job that no worker had ever
claimed. A worker that claimed it and then died (OOM, `docker stop`, a hard
time-limit kill) left status PROCESSING with a stale `locked_at`, which matched
nothing; the item stayed `processing` forever, and `_pending_item_ids` skips
`processing` items, so even a republish would have published nothing.

Now: a live worker refreshes `locked_at` (JobHeartbeat); the dispatcher treats a
lock that stopped being refreshed as a lost worker, counts it as an attempt
(so a job that kills its worker cannot loop forever), and puts the item back
where the normal retry path can see it.
"""
import os
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import database as db_module
from app.services import outbox

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

URL = "https://example.com/a-long-video"


@pytest.fixture
def db():
    name = f"fb_lost_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = create_engine(url, pool_pre_ping=True)
    try:
        from alembic import command

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
def engine_in_use(db):
    """Point the app's SessionLocal at the throwaway database."""
    previous = (db_module._engine, db_module._sessionmaker)
    db_module._engine, db_module._sessionmaker = db, None
    yield
    db_module._engine, db_module._sessionmaker = previous


class _User:
    def __init__(self, uid):
        self.id = uid


@pytest.fixture
def saved(sessions, monkeypatch):
    """A real save: user, asset, item, memory and PENDING job."""
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest
    from app.tasks import process_item

    monkeypatch.setattr(process_item, "delay", lambda *_a, **_k: None)
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": str(uid), "e": f"lost-{uid.hex[:8]}@example.test"})
        s.commit()
        result = ingest(IngestRequest(url=URL, title_hint="A page"), s, _User(uid))
        s.commit()
        return str(result.id)


def _crash(sessions, item_id, locked="10 minutes", attempts=0):
    """What a dead worker leaves behind."""
    with sessions() as s:
        s.execute(text("UPDATE items SET status = 'processing' WHERE id = :i"),
                  {"i": item_id})
        s.execute(text(f"""
            UPDATE processing_jobs SET status = 'PROCESSING',
                   claimed_at = now() - interval '{locked}',
                   locked_at = now() - interval '{locked}',
                   attempt_count = :a
            WHERE content_id = (SELECT content_id FROM items WHERE id = :i)"""),
                  {"i": item_id, "a": attempts})
        s.commit()


def _state(sessions, item_id):
    with sessions() as s:
        item = s.execute(text("SELECT status, failure_reason FROM items WHERE id = :i"),
                         {"i": item_id}).mappings().one()
        job = s.execute(text("""
            SELECT status, attempt_count, locked_at, last_error FROM processing_jobs
            WHERE content_id = (SELECT content_id FROM items WHERE id = :i)"""),
                        {"i": item_id}).mappings().one()
    return dict(item), dict(job)


def test_a_lost_worker_is_detected_and_the_save_goes_back_to_pending(sessions, saved):
    _crash(sessions, saved)
    with sessions() as s:
        assert outbox.recover_lost_jobs(s) == 1
    item, job = _state(sessions, saved)
    assert item["status"] == "pending"
    assert job["status"] == "PENDING" and job["attempt_count"] == 1
    assert job["locked_at"] is None and "worker lost" in job["last_error"]


def test_the_dispatcher_republishes_a_recovered_save(sessions, saved):
    _crash(sessions, saved)
    published = []
    with sessions() as s:
        assert outbox.dispatch_once(s, publisher=published.append)["recovered"] == 1
    assert published == []  # backing off, not hammering
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET available_at = now() - interval '1 second'"))
        s.commit()
        outbox.dispatch_once(s, publisher=published.append)
    assert published == [saved]
    assert _state(sessions, saved)[1]["status"] == "PROCESSING"


def test_a_worker_that_is_still_heartbeating_is_left_alone(sessions, saved):
    _crash(sessions, saved, locked="5 seconds")
    with sessions() as s:
        assert outbox.recover_lost_jobs(s) == 0
    item, job = _state(sessions, saved)
    assert item["status"] == "processing" and job["status"] == "PROCESSING"
    assert job["attempt_count"] == 0


def test_a_job_that_keeps_killing_its_worker_ends_in_failed(sessions, saved):
    _crash(sessions, saved, attempts=outbox.max_attempts() - 1)
    with sessions() as s:
        outbox.recover_lost_jobs(s)
    item, job = _state(sessions, saved)
    assert job["status"] == "FAILED"
    assert item["status"] == "failed" and "worker lost" in item["failure_reason"]


def test_the_heartbeat_keeps_the_lock_fresh_and_stops_cleanly(sessions, saved, engine_in_use):
    _crash(sessions, saved)  # an old lock, as if the claim were 10 minutes ago
    with sessions() as s:
        job_id = s.execute(text("SELECT id FROM processing_jobs")).scalar()
    beat = outbox.JobHeartbeat(job_id, interval=0.05).start()
    try:
        time.sleep(0.4)
    finally:
        beat.stop()
    assert not beat._thread.is_alive()
    with sessions() as s:
        age = s.execute(text("SELECT extract(epoch FROM now() - locked_at) "
                             "FROM processing_jobs")).scalar()
    assert age < 5
    # and it no longer touches the job once the worker is done with it
    _crash(sessions, saved)
    time.sleep(0.2)
    with sessions() as s:
        assert s.execute(text("SELECT extract(epoch FROM now() - locked_at) "
                              "FROM processing_jobs")).scalar() > 500
