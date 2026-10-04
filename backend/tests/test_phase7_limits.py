"""Phase 7: controlled queue concurrency.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase7_limits.py -q
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
    reason="TEST_DATABASE_URL not set; skipping Phase 7 limit tests",
)

BURSTS = [1, 10, 50, 100]


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
    name = f"fb_phase7_{uuid.uuid4().hex[:10]}"
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
    from app.tasks import process_item

    published = []
    monkeypatch.setattr(process_item, "delay", published.append)
    return published


@pytest.fixture(autouse=True)
def reset_limits():
    from app.services.limits import reset_all_stats

    reset_all_stats()
    yield
    reset_all_stats()


# --- the limits themselves ------------------------------------------------

def test_each_stage_has_its_own_budget():
    from app.services.limits import AI_LIMIT, EMBEDDING_LIMIT, FETCH_LIMIT

    limits = {FETCH_LIMIT.name, AI_LIMIT.name, EMBEDDING_LIMIT.name}
    assert limits == {"fetch", "ai", "embedding"}, \
        "concurrency must be limited separately per stage"
    for limiter in (FETCH_LIMIT, AI_LIMIT, EMBEDDING_LIMIT):
        assert limiter.limit >= 1


def test_stage_concurrency_is_never_exceeded():
    """The limiter really bounds simultaneity, not just configuration."""
    from app.services.limits import AI_LIMIT, EMBEDDING_LIMIT, FETCH_LIMIT

    for limiter in (FETCH_LIMIT, AI_LIMIT, EMBEDDING_LIMIT):
        limiter.reset_stats()
        peak_holder = []
        lock = threading.Lock()
        barrier = threading.Barrier(limiter.limit * 4)

        def worker():
            barrier.wait(timeout=30)
            with limiter:
                with lock:
                    peak_holder.append(limiter.current)
                threading.Event().wait(0.005)

        threads = [threading.Thread(target=worker)
                   for _ in range(limiter.limit * 4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert limiter.peak <= limiter.limit, \
            f"{limiter.name} peaked at {limiter.peak} > {limiter.limit}"
        assert limiter.peak > 1, "the threads should actually have overlapped"


def test_rate_limiter_caps_calls_per_second():
    from app.services.limits import rate_limiter_for

    bucket = rate_limiter_for("groq")
    bucket.reset_stats()
    for _ in range(bucket.burst):
        assert bucket.try_acquire() is True
    # The bucket is full, so the next call must be refused rather than sent.
    assert bucket.try_acquire() is False
    assert bucket.peak_in_window <= bucket.burst


# --- bursts of 1 / 10 / 50 / 100 queued jobs ------------------------------

class _UserRow:
    def __init__(self, uid):
        self.id = uid


def _seed_jobs(db, sessions, count):
    """Create `count` independent items, each with its own content and job."""
    import app.models as models
    from app.services.outbox import record_job

    users = []
    with sessions() as s:
        for index in range(count):
            uid = uuid.uuid4()
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"b{index}@example.test"})
            users.append(uid)
        s.commit()

    created = []
    for index, uid in enumerate(users):
        with sessions() as s:
            asset = models.ContentAsset(
                canonical_url=f"https://example.test/page-{index}",
                dedupe_key=f"url:https://example.test/page-{index}",
                owner_user_id=uid, visibility="UNKNOWN")
            s.add(asset)
            s.flush()
            # Phase 16: items_user_content_fk requires this row to exist before
            # an item may name the asset. Real saves always create it, so the
            # fixture creates it too. Setup only.
            s.add(models.UserMemory(user_id=uid, content_id=asset.id))
            s.flush()
            item = models.Item(
                user_id=uid, url=asset.canonical_url,
                canonical_url=asset.canonical_url, title=f"Page {index}",
                content_id=asset.id)
            s.add(item)
            s.flush()
            record_job(s, asset.id, models.JOB_TYPE_PROCESS)
            s.commit()
            created.append((str(item.id), str(asset.id)))
    return created


def _run_worker(item_id, db, embed=None):
    """Run the real worker for one item, wired to the test database."""
    import app.tasks as app_tasks
    from app import database as db_module
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    async def _fetch(url, preview=""):
        return {"text": "some content here", "title": "T",
                "source_type": "article"}

    async def _extract(raw_text, url_title="", url=""):
        return Brief(title="T", overview="s", highlights=["k"], topics=["t"],
                     intent=["read"], structured_data={"content_type": "article"})

    async def _embed_many(texts, task="document"):
        return [[0.0] * 1536 for _ in texts]

    # The stages live in app.services.pipeline and call through the service
    # modules, so that is what a test must replace.
    embedder.embed_many = embed or _embed_many
    fetcher.fetch_content = _fetch
    extractor.extract_brief = _extract
    storage.store_raw_snapshot = lambda i, p: None
    embedder.chunk_text_with_timestamps = (
        lambda text, size=800, overlap=160: [{"chunk_idx": 0, "chunk_text": text,
                                            "start_timestamp": None,
                                            "start_seconds": None}])
    embedder.embedding_model_name = lambda: "test-model"

    previous = db_module._engine
    previous_factory = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = None
    try:
        app_tasks.process_item.apply(args=(item_id,), throw=False)
    finally:
        db_module._engine = previous
        db_module._sessionmaker = previous_factory
@pytest.mark.parametrize("count", BURSTS)
def test_burst_of_queued_jobs_respects_limits_and_terminates(db, sessions, count):
    """1, 10, 50 and 100 queued jobs: limits respected, every job terminal.

    Each job is handed to a worker exactly as the queue would, all at once.
    Whatever the burst size, no job may be left mid-flight and none may loop
    forever: every one ends READY or FAILED.
    """
    from app.services.limits import AI_LIMIT, EMBEDDING_LIMIT, FETCH_LIMIT

    created = _seed_jobs(db, sessions, count)
    with sessions() as s:
        s.execute(text("UPDATE processing_jobs SET available_at=now()"))
        s.commit()

    errors = []

    def worker(item_id):
        try:
            _run_worker(item_id, db)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(item_id,))
               for item_id, _ in created]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, f"burst of {count} raised: {errors[:3]}"

    with sessions() as s:
        statuses = [row[0] for row in s.execute(
            text("SELECT status FROM processing_jobs")).fetchall()]
        active = s.execute(text(
            "SELECT count(*) FROM processing_jobs "
            "WHERE status IN ('PENDING','PROCESSING')")).scalar()

    assert len(statuses) == count, "every job must still exist"
    assert set(statuses) <= {"READY", "FAILED"}, \
        f"burst left jobs mid-flight: {sorted(set(statuses))}"
    assert active == 0, f"{active} jobs never reached a terminal state"
    for limiter in (FETCH_LIMIT, AI_LIMIT, EMBEDDING_LIMIT):
        assert limiter.peak <= limiter.limit, \
            f"{limiter.name} peaked at {limiter.peak} > {limiter.limit}"


def test_retry_is_bounded_and_ends_in_failed(db, sessions, monkeypatch):
    """Past the attempt cap a job becomes FAILED instead of retrying forever."""
    import app.tasks as app_tasks
    from app.services import embedder, outbox

    monkeypatch.setattr(outbox, "DEFAULT_MAX_ATTEMPTS", 3)
    item_id = _seed_jobs(db, sessions, 1)[0][0]

    async def _boom(texts, task="document"):
        raise RuntimeError("provider down")

    monkeypatch.setattr(embedder, "embed_many", _boom)

    for _ in range(5):
        _run_worker(item_id, db, embed=_boom)
        with sessions() as s:
            s.execute(text("UPDATE processing_jobs SET available_at=now()"))
            s.commit()

    with sessions() as s:
        job = s.execute(text(
            "SELECT status, attempt_count, last_error FROM processing_jobs")
        ).mappings().one()
    assert job["status"] == "FAILED", "the cap must be enforced"
    assert job["attempt_count"] == 3, "attempts must stop at the cap"
    assert "provider down" in job["last_error"]


def test_backoff_grows_between_attempts():
    from app.services.outbox import BASE_BACKOFF, MAX_BACKOFF, backoff_for

    delays = [backoff_for(n) for n in range(1, 6)]
    assert delays[0] == BASE_BACKOFF
    for earlier, later in zip(delays, delays[1:]):
        assert later > earlier, f"backoff did not grow: {delays}"
    assert backoff_for(50) == MAX_BACKOFF, "backoff must be capped"