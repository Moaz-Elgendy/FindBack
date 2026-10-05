"""A save must never be stranded by the recovery path.

Two claims, both checked against the real worker and the real dispatcher code:

* With NO provider keys configured the pipeline still finishes. The extractor
  falls back to the offline heuristic and the item reaches `ready` with a real
  summary. This is what the "you had not added an API key" hypothesis predicted,
  and it already held -- the missing key was never the reason an item hung.

* A save whose worker attempt raised is retried by the dispatcher. It was not.
  A failed attempt leaves the item `failed` and the job back to PENDING, and the
  dispatcher only publishes items that are still `pending`. So it claimed that
  job, published NOTHING, and still marked the job PROCESSING -- and because the
  dispatcher only ever claims PENDING jobs, nothing would ever publish or retry
  it again. The save was stuck for good.

    TEST_DATABASE_URL=... python -m pytest tests/test_stuck_save_recovery.py -q
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping the stuck-save tests",
)

URL = "https://example.test/a-page-without-any-keys"


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
    name = f"fb_stuck_{uuid.uuid4().hex[:10]}"
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


class _UserRow:
    def __init__(self, uid):
        self.id = uid


@pytest.fixture
def user(sessions):
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": str(uid), "e": f"stuck-{uid.hex[:8]}@example.test"})
        s.commit()
    return uid


@pytest.fixture
def queue(monkeypatch):
    """A working broker: the save's fast path publishes, nothing is sent twice."""
    from app.tasks import process_item

    published = []
    monkeypatch.setattr(process_item, "delay", published.append)
    return published


def _save(session, user_id, url=URL):
    """A real save through the ingest handler."""
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint="A page",
                                  preview="the text the share sheet gave us"),
                    session, _UserRow(user_id))
    session.commit()
    return str(result.id)


def _state(sessions, item_id):
    """What a user or an operator can see: the item, and its processing job."""
    with sessions() as s:
        item = s.execute(text("SELECT status FROM items WHERE id = :i"),
                         {"i": str(item_id)}).mappings().one()
        job = s.execute(text(
            "SELECT status, attempt_count, last_stage, last_error "
            "FROM processing_jobs "
            "WHERE content_id = (SELECT content_id FROM items WHERE id = :i)"),
            {"i": str(item_id)}).mappings().one()
    return item["status"], dict(job)


def _run_worker(sessions, item_id):
    """Run the real Celery task body once, with Celery's own retry disabled.

    One call is one attempt: the retry machinery is exactly what these tests
    have to observe rather than hide behind.
    """
    from app import database as db_module
    from app import tasks as app_tasks

    previous_engine = db_module._engine
    previous_sessionmaker = db_module._sessionmaker
    db_module._engine = sessions.kw["bind"]
    db_module._sessionmaker = None
    previous_retries = app_tasks.process_item.max_retries
    app_tasks.process_item.max_retries = 0
    try:
        app_tasks.process_item.apply(args=(str(item_id),), throw=False)
    finally:
        app_tasks.process_item.max_retries = previous_retries
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_sessionmaker


def _dispatch(sessions):
    """One dispatcher tick, recording what it would have handed to the queue."""
    from app.services.outbox import dispatch_once

    published = []
    with sessions() as s:
        # Time has passed past the backoff the last failure set.
        s.execute(text("UPDATE processing_jobs SET available_at = now()"))
        s.commit()
        stats = dispatch_once(s, publisher=published.append)
    return stats, published


def _break_embedder(monkeypatch):
    """A stage that fails in a way nothing inside the pipeline catches.

    An embedding transport error that is not an `AIError` is the realistic
    shape: `ai.embed_texts` swallows its own provider failures, so anything else
    -- a driver error, a cancellation, a bug -- reaches the task's failure
    handler and must still leave the save recoverable.
    """
    from app.services import embedder

    async def _boom(texts, task="document"):
        raise RuntimeError("embedding transport blew up")

    monkeypatch.setattr(embedder, "embed_many", _boom)


def test_with_no_provider_keys_the_item_still_reaches_ready(sessions, user,
                                                            queue, monkeypatch):
    """No key set: the offline heuristic brief, a `ready` item, a real summary.

    This is the expectation the bug report started from. It holds, which is why
    the missing key is not the cause of a stuck item.
    """
    from app.services import ai, fetcher, storage

    # Provenance: nothing may reach a provider.
    assert ai.chat_config() is None, "this test only means anything with no key"
    assert ai.embedding_config() is None

    async def fetch(url, preview=""):
        return {"text": "How to roll back a kubernetes deployment. "
                        "Use kubectl rollout undo, then check the pods.",
                "title": "Rollback", "source_type": "article"}

    monkeypatch.setattr(fetcher, "fetch_content", fetch)
    monkeypatch.setattr(storage, "store_raw_snapshot",
                        lambda item_id, payload: None)

    with sessions() as s:
        item_id = _save(s, user)
    assert queue == [item_id], "the save published its work to the queue"

    _run_worker(sessions, item_id)

    item_status, job = _state(sessions, item_id)
    assert item_status == "ready", "an offline save still finishes"
    assert job["status"] == "READY"
    assert job["last_stage"] == "EMBED", "the whole pipeline ran"
    assert job["last_error"] is None

    with sessions() as s:
        summary = s.execute(text("SELECT summary FROM items WHERE id = :i"),
                            {"i": item_id}).scalar()
    assert summary, "the heuristic brief produced a summary the app can show"
    assert "Saved content awaiting AI processing" not in summary, \
        "the placeholder is replaced by the heuristic's own overview"


def test_a_failed_attempt_is_republished_by_the_dispatcher(sessions, user,
                                                          queue, monkeypatch):
    """The dispatcher republishes a save whose worker attempt raised."""
    with sessions() as s:
        item_id = _save(s, user)

    _break_embedder(monkeypatch)
    _run_worker(sessions, item_id)

    item_status, job = _state(sessions, item_id)
    assert job["status"] == "PENDING", "a retryable failure stays retryable"
    assert job["attempt_count"] == 1
    assert job["last_error"] and "blew up" in job["last_error"]

    _stats, published = _dispatch(sessions)

    assert published == [item_id], (
        "the stranded save must be republished; before the fix the dispatcher "
        "found no `pending` item, published nothing, and still marked the job "
        "PROCESSING, which stranded it permanently")
    item_status, job = _state(sessions, item_id)
    assert job["status"] == "PROCESSING"
    assert item_status == "failed", "still failed until the worker runs again"


def test_the_dispatcher_never_consumes_a_job_it_published_nothing_for(
        sessions, user, queue):
    """Nothing to publish means the job stays claimable, not PROCESSING.

    The dispatcher only ever claims PENDING jobs, so moving one to PROCESSING
    without publishing anything is unrecoverable.
    """
    with sessions() as s:
        item_id = _save(s, user)
        # A worker already owns this item and is mid-run, so there is nothing
        # for the dispatcher to publish.
        s.execute(text("UPDATE items SET status = 'processing' WHERE id = :i"),
                  {"i": item_id})
        s.execute(text("UPDATE processing_jobs SET status = 'PENDING', "
                       "locked_at = NULL"))
        s.commit()

    stats, published = _dispatch(sessions)

    assert published == [], "a `processing` item must not be published again"
    assert stats["published"] == 0, "nothing was published, so nothing was sent"
    _item_status, job = _state(sessions, item_id)
    assert job["status"] == "PENDING", (
        "a job with nothing to publish must stay claimable; moving it to "
        "PROCESSING strands it, because no later dispatcher tick sees it")


def test_a_save_that_keeps_failing_eventually_parks_as_failed(sessions, user,
                                                               queue, monkeypatch):
    """The retry loop is bounded: it ends FAILED, and it does end at all."""
    from app.services import outbox

    monkeypatch.delenv("JOB_MAX_ATTEMPTS", raising=False)
    monkeypatch.setattr(outbox, "DEFAULT_MAX_ATTEMPTS", 3)

    with sessions() as s:
        item_id = _save(s, user)
    _break_embedder(monkeypatch)

    # The first attempt came from the save's own fast path, which published to a
    # healthy queue and marked the job PROCESSING. The dispatcher is only
    # involved from the second attempt on, which is the whole point: after the
    # first failure the job is PENDING again and only a tick can restart it.
    _run_worker(sessions, item_id)
    item_status, job = _state(sessions, item_id)
    assert job["status"] == "PENDING", "a failed attempt is handed back"

    for _ in range(10):
        if job["status"] in ("READY", "FAILED"):
            break
        _stats, published = _dispatch(sessions)
        assert published == [item_id], \
            "each retry must actually reach the queue"
        for published_id in published:
            _run_worker(sessions, published_id)
        item_status, job = _state(sessions, item_id)

    assert job["status"] == "FAILED", "the cap must stop the retry loop"
    assert job["attempt_count"] == 3, "attempts stop at the cap"
    assert item_status == "failed"


@pytest.mark.parametrize("age_minutes,worker_claimed,expected", [
    (10, False, True), (1, False, False), (10, True, False),
])
def test_dispatch_recovers_only_stale_unclaimed_publications(
        sessions, user, queue, age_minutes, worker_claimed, expected):
    """Recover a lost message while preserving recent sends and worker locks."""
    from app.services.outbox import dispatch_once

    with sessions() as s:
        item_id = _save(s, user)
        s.execute(text(
            "UPDATE processing_jobs SET updated_at = now() - "
            "make_interval(mins => :age), "
            "locked_at = CASE WHEN :claimed THEN now() ELSE NULL END, "
            "claimed_at = CASE WHEN :claimed THEN now() ELSE NULL END"),
            {"age": age_minutes, "claimed": worker_claimed})
        s.commit()
        published = []
        stats = dispatch_once(s, publisher=published.append)

    assert published == ([item_id] if expected else [])
    assert stats["published"] == int(expected)
    assert _state(sessions, item_id)[1]["status"] == "PROCESSING"
    with sessions() as s:
        assert dispatch_once(s, publisher=published.append)["published"] == 0


def _age_publication(session):
    session.execute(text("UPDATE processing_jobs SET updated_at = now() - "
                         "interval '10 minutes', available_at = now()"))
    session.commit()


def test_unclaimed_republishing_is_bounded(sessions, user, queue, monkeypatch):
    from app.services.outbox import dispatch_once

    monkeypatch.setenv("JOB_MAX_ATTEMPTS", "3")
    with sessions() as s:
        item_id = _save(s, user)
        published = []
        for attempt in range(1, 4):
            _age_publication(s)
            dispatch_once(s, publisher=published.append)
            job = s.execute(text("SELECT status, attempt_count, last_error, "
                                 "available_at > now() AS backed_off "
                                 "FROM processing_jobs")).mappings().one()
            assert job["attempt_count"] == attempt
            assert job["status"] == ("FAILED" if attempt == 3 else "PROCESSING")
            if attempt < 3:
                assert job["backed_off"]
        assert published == [item_id] * 3
        assert job["last_error"] == "never claimed by a worker after 3 republishes"
        _age_publication(s)
        assert dispatch_once(s, publisher=published.append)["claimed"] == 0


def test_empty_stale_publication_waits_before_rechecking(sessions, user, queue):
    from app.services.outbox import dispatch_once

    with sessions() as s:
        _save(s, user)
        s.execute(text("UPDATE items SET status = 'ready'"))
        _age_publication(s)
        published = []
        assert dispatch_once(s, publisher=published.append)["skipped"] == 1
        assert dispatch_once(s, publisher=published.append)["claimed"] == 0
        assert published == []
        _age_publication(s)
        assert dispatch_once(s, publisher=published.append)["skipped"] == 1


def test_permanently_failing_publisher_is_bounded(sessions, user, queue, monkeypatch):
    from app.services.outbox import dispatch_once

    monkeypatch.setenv("JOB_MAX_ATTEMPTS", "3")
    calls = []

    def fail(item_id):
        calls.append(item_id)
        raise RuntimeError("broker unavailable")

    with sessions() as s:
        item_id = _save(s, user)
        s.execute(text("UPDATE processing_jobs SET status = 'PENDING'"))
        for attempt in range(1, 4):
            _age_publication(s)
            assert dispatch_once(s, publisher=fail)["failed"] == 1
            job = s.execute(text("SELECT status, attempt_count, last_error, "
                                 "locked_at, available_at > now() AS backed_off "
                                 "FROM processing_jobs")).mappings().one()
            assert job["attempt_count"] == attempt
            assert job["status"] == ("FAILED" if attempt == 3 else "PENDING")
            assert job["locked_at"] is None
            if attempt < 3:
                assert job["backed_off"]
        assert "broker unavailable" in job["last_error"]
        _age_publication(s)
        assert dispatch_once(s, publisher=fail)["claimed"] == 0
        assert calls == [item_id] * 3


@pytest.mark.parametrize("status_code,expected", [(401, "READY"), (503, "FAILED")])
def test_fetch_outcomes_finish_or_retry_bounded(sessions, user, queue, monkeypatch, status_code, expected):
    import httpx
    from app.services import fetcher, storage
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    monkeypatch.setenv("JOB_MAX_ATTEMPTS", "2")
    client = httpx.AsyncClient
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kw: client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status_code)), **kw))
    monkeypatch.setattr(storage, "store_raw_snapshot", lambda *args: None)
    with sessions() as session:
        item_id = _save(session, user)
        session.execute(text("UPDATE items SET raw_preview = NULL WHERE id = :i"), {"i": item_id})
        session.commit()
    _run_worker(sessions, item_id)
    if status_code == 503:
        state, job = _state(sessions, item_id)
        assert job["status"] == "PENDING"
        assert job["attempt_count"] == 1
        _dispatch(sessions)
        _run_worker(sessions, item_id)
    state, job = _state(sessions, item_id)
    assert job["status"] == expected
    if status_code == 401:
        assert state == "ready"
        assert job["attempt_count"] == 1  # completion counts the successful attempt
        with sessions() as session:
            row = session.execute(text("SELECT summary, search_text, fetch_metadata FROM items WHERE id=:i"), {"i": item_id}).mappings().one()
        assert "could not be read" in row["summary"]
        assert URL in row["search_text"]
        assert row["fetch_metadata"]["input_provenance"] == "none"
    else:
        assert job["attempt_count"] == 2
        assert _dispatch(sessions)[1] == []
