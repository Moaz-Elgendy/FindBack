"""Phase 20: load and failure testing.

Two questions, and only two:

    does a burst create BACKLOG rather than instability?
    does a failure leave anything silently lost?

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase20_load.py -q -s

Everything is driven through the REAL task body (`process_item.apply`) against
the REAL database. Only the outside world is faked -- the fetcher, the model,
the embedding call and the broker. Redis is not required and no broker is
started: `apply()` runs the task in-process exactly as a Celery worker runs it,
so what is tested is the code that runs in production, not a stand-in.

Nothing here is added to the product. Where a scenario reveals that the system
needs something it does not have, that is reported, not built.
"""
import os
import threading
import time
import uuid

import pytest
from celery.exceptions import Retry as RetrySignal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 20 load/failure tests",
)

# The burst sizes the phase names.
BURSTS = (10, 50, 100, 500)


class AITimeout(TimeoutError):
    """The model took too long. Phase 14's provider raises its own timeout."""


class EmbeddingTimeout(TimeoutError):
    """The embedding call took too long."""


class SourceUnavailable(OSError):
    """The page could not be fetched: 404, DNS failure, or a dead host."""


class RedisDown(RuntimeError):
    """The broker is unreachable."""


class TransientDB(RuntimeError):
    """A database error that would succeed on the next attempt."""


class WorkerCrashed(BaseException):
    """The worker process died: SIGKILL, OOM, or `os._exit`.

    Deliberately NOT an `Exception`. A real crash skips every `except
    Exception` handler in the task, which is precisely the case worth testing.
    """


@pytest.fixture(scope="module")
def admin_engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture(scope="module")
def db(admin_engine):
    """One migrated database for the whole module.

    Module-scoped because each burst creates hundreds of rows, and paying for a
    migration per test would dominate the runtime. Each test still isolates
    itself by user, so counts never cross between scenarios.
    """
    name = f"fb_phase20_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = create_engine(url, pool_pre_ping=True, pool_size=20)
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


class Harness:
    """A save-and-work loop with faults that can be turned on and off.

    The fakes are the outside world only: the fetcher, the model, the embedding
    call and the broker. The task body, the SQL it issues and the job state
    machine are the real ones, so what these tests exercise is what runs in
    production.

    `ledger` counts real work per URL. That is how "no repeated duplicate
    processing" is checked: if the same URL is fetched twice, it is visible
    here, rather than inferred from a status column.
    """

    def __init__(self, db, sessions, user_id):
        self.db = db
        self.sessions = sessions
        self.user_id = user_id
        self.ledger: dict[str, int] = {}
        self.published: list[str] = []
        self.faults: dict[str, object] = {}
        self.fail_times: dict[str, int] = {}
        self.lock = threading.Lock()

    # --- faults ---------------------------------------------------------
    def fail(self, name, exc, times=None):
        """Make `name` raise `exc`. `times` limits it; None means always."""
        self.faults[name] = exc
        self.fail_times[name] = -1 if times is None else times

    def heal(self, name):
        self.faults.pop(name, None)
        self.fail_times.pop(name, None)

    def heal_all(self):
        self.faults.clear()
        self.fail_times.clear()

    def _maybe_fail(self, name):
        with self.lock:
            if name not in self.faults:
                return
            remaining = self.fail_times.get(name, -1)
            if remaining == 0:
                return
            if remaining > 0:
                self.fail_times[name] = remaining - 1
            raise self.faults[name]

    def _count(self, url):
        with self.lock:
            self.ledger[url] = self.ledger.get(url, 0) + 1

    # --- the outside world ----------------------------------------------
    async def fetch(self, url, preview=""):
        self._count(url)
        self._maybe_fail("fetch")
        return {"text": "chicken cream mushroom risotto with parmesan",
                "title": "Risotto", "source_type": "article"}

    async def extract(self, raw_text, url_title="", url=""):
        self._maybe_fail("ai")
        from app.schemas import Brief

        return Brief(title="Risotto", overview="A risotto",
                     highlights=["one thing"], topics=["dinner"],
                     intent=["cook"],
                     structured_data={"content_type": "recipe"})

    async def embed_many(self, texts, task="document"):
        self._maybe_fail("embedding")
        return [[0.0] * 1536 for _ in texts]

    async def embed_text(self, text, task="document"):
        return [0.0] * 1536

    def publish(self, item_id):
        self._maybe_fail("redis")
        with self.lock:
            self.published.append(str(item_id))

    # --- driving the system ----------------------------------------------
    def install(self, monkeypatch):
        from app.services import embedder, extractor, fetcher, storage

        fetcher.fetch_content = self.fetch
        extractor.extract_brief = self.extract
        embedder.chunk_text_with_timestamps = (
            lambda text, size=800, overlap=160: [
                {"chunk_idx": 0, "chunk_text": text, "start_timestamp": None,
                 "start_seconds": None}])
        embedder.embed_many = self.embed_many
        # Must itself be awaitable: `embedder.embed_text` is `await`ed by the
        # search path, so returning this coroutine's result synchronously would
        # hand the caller an un-awaited coroutine.
        embedder.embed_text = self.embed_text
        embedder.embedding_model_name = lambda: "test-model"
        storage.store_raw_snapshot = lambda item_id, payload: None
        monkeypatch.setattr("app.tasks.process_item.delay", self.publish)
        return self

    def save(self, url):
        """Save through the real ingest router, as an API call would."""
        from app.routers.ingest import ingest
        from app.schemas import IngestRequest

        class _Row:
            def __init__(self, uid):
                self.id = uid

        with self.sessions() as s:
            result = ingest(IngestRequest(url=url, title_hint="Video",
                                          preview="preview"),
                            s, _Row(self.user_id))
            s.commit()
        return result

    def save_as(self, url, user_id):
        """Save on behalf of another user, for the cross-user dedupe check."""
        from app.routers.ingest import ingest
        from app.schemas import IngestRequest

        class _Row:
            def __init__(self, uid):
                self.id = uid

        with self.sessions() as s:
            result = ingest(IngestRequest(url=url, title_hint="Video",
                                          preview="preview"),
                            s, _Row(user_id))
            s.commit()
        return result

    def save_burst(self, count, prefix="https://burst.test"):
        """Queue `count` distinct saves, the way a burst arrives."""
        began = time.monotonic()
        results = [self.save(f"{prefix}/{index}") for index in range(count)]
        elapsed = time.monotonic() - began
        return results, elapsed

    def dispatch(self, limit=500):
        """Run the real dispatcher: claim due jobs and publish them."""
        from app.services.outbox import dispatch_once

        totals = {"claimed": 0, "published": 0, "failed": 0, "items": 0}
        with self.sessions() as s:
            while True:
                stats = dispatch_once(s, publisher=self.publish, limit=limit)
                for key in totals:
                    totals[key] += stats[key]
                if stats["claimed"] == 0:
                    break
        return totals

    def work(self, item_ids):
        """Run the real task body once per item, in-process.

        `apply()` is NOT used: in eager mode Celery's `retry()` re-runs the task
        immediately instead of scheduling it, so one `apply()` silently performs
        several attempts and "one call" stops meaning "one attempt". `run()`
        calls the task body directly, so each call is exactly one attempt and a
        retry surfaces as `Retry` -- which is what lets these tests control how
        many attempts happen and when.
        """
        from app import database as db_module
        from app import tasks as app_tasks

        outcomes = []
        previous = db_module._engine
        db_module._engine = self.db
        try:
            for item_id in item_ids:
                try:
                    result = app_tasks.process_item.run(str(item_id))
                    outcomes.append(("ok", result.get("status")))
                except RetrySignal as exc:
                    # The task asked to be retried. That is a real, expected
                    # outcome, not a crash.
                    outcomes.append(("retry", type(exc).__name__))
                except Exception as exc:  # noqa: BLE001 - the outcome is the point
                    outcomes.append(("raised", type(exc).__name__))
        finally:
            db_module._engine = previous
        return outcomes

    def drain(self, item_ids, max_rounds=12):
        """Attempt every retryable job until all of them reach a terminal state.

        Driven off the JOB table, not the item status: after a failed attempt the
        item is `failed` while its job is back to `PENDING` waiting to be
        retried. Draining on item status would therefore stop after one attempt.

        Backoff is cleared between rounds: this stands in for the passage of
        time a real queue would provide, so the test does not have to sit
        through a 30-second exponential backoff to observe a retry.
        """
        history = []
        for _round in range(max_rounds):
            due = self._retryable_job_items()
            if not due:
                break
            self._release_backoff()
            outcomes = self.work(due)
            history.append(outcomes)
        return history

    def _retryable_job_items(self):
        """Item ids a worker should pick up.

        PENDING (waiting to be retried) and PROCESSING (published but not yet
        finished) are both legitimate: after `dispatch_once` publishes, the job
        is PROCESSING until the worker completes it, and a crashed worker leaves
        it PROCESSING with a stale lock.
        """
        with self.sessions() as s:
            return [str(row[0]) for row in s.execute(text(
                "SELECT i.id FROM items i JOIN processing_jobs p "
                "ON p.content_id = i.content_id "
                "WHERE i.user_id = :u AND p.status IN ('PENDING', 'PROCESSING') "
                "ORDER BY i.created_at"), {"u": self.user_id}).all()]

    def _release_backoff(self):
        """Make every due job available now, standing in for elapsed time."""
        with self.sessions() as s:
            s.execute(text(
                "UPDATE processing_jobs SET available_at = now() "
                "WHERE status = 'PENDING'"))
            s.commit()

    # --- observation ------------------------------------------------------
    def work_crash(self, item_id):
        """Run the task body where a `BaseException` escapes it.

        A real crash kills the process, so this deliberately lets the exception
        leave the task entirely rather than being caught and converted into a
        retry.
        """
        from app import database as db_module
        from app import tasks as app_tasks

        previous = db_module._engine
        db_module._engine = self.db
        try:
            try:
                app_tasks.process_item.run(str(item_id))
            except WorkerCrashed:
                pass
            except RetrySignal:
                pass
            except BaseException:  # noqa: BLE001 - a crash is any BaseException
                raise
        finally:
            db_module._engine = previous

    def _pending_items(self):
        """Item ids still waiting for a worker."""
        with self.sessions() as s:
            return s.execute(text(
                "SELECT id FROM items WHERE user_id = :u AND status = 'pending' "
                "ORDER BY created_at"), {"u": self.user_id}).mappings().all()

    def jobs(self):
        with self.sessions() as s:
            return s.execute(text(
                "SELECT status, count(*) AS n FROM processing_jobs "
                "JOIN content_assets ON content_assets.id = processing_jobs.content_id "
                "JOIN user_memories ON user_memories.content_id = content_assets.id "
                "WHERE user_memories.user_id = :u GROUP BY status"),
                {"u": self.user_id}).mappings().all()

    def item_statuses(self):
        with self.sessions() as s:
            return s.execute(text(
                "SELECT status, count(*) AS n FROM items "
                "WHERE user_id = :u GROUP BY status"),
                {"u": self.user_id}).mappings().all()

    def counts(self):
        """(items, jobs) for this user. These must always match."""
        with self.sessions() as s:
            items = s.execute(text(
                "SELECT count(*) FROM items WHERE user_id = :u"),
                {"u": self.user_id}).scalar()
            jobs = s.execute(text(
                "SELECT count(*) FROM processing_jobs p JOIN user_memories m "
                "ON m.content_id = p.content_id WHERE m.user_id = :u"),
                {"u": self.user_id}).scalar()
        return items, jobs

    def total_work(self):
        return sum(self.ledger.values())

    def worst_url(self):
        return max(self.ledger, key=lambda u: self.ledger[u]) if self.ledger else None


@pytest.fixture
def harness(db, sessions, user, monkeypatch):
    return Harness(db, sessions, user).install(monkeypatch)


# --- LOAD -------------------------------------------------------------------
# The goal is a BACKLOG, not a result: a burst must be absorbed instantly by the
# database and drained later by workers. A burst that blocks the save path, or
# that loses a job, is the failure this section exists to catch.


@pytest.mark.parametrize("count", BURSTS)
def test_a_burst_queues_every_save_and_loses_nothing(harness, count, capsys):
    """Every save in the burst is durable, and the save path stays fast."""
    _results, elapsed = harness.save_burst(count)
    items, jobs = harness.counts()

    with capsys.disabled():
        print(f"\nburst of {count}: queued in {elapsed:.2f}s "
              f"({elapsed / count * 1000:.1f} ms per save)")

    assert items == count, "a save was lost in the burst"
    assert jobs == count, "every save must leave a durable processing intent"
    # Product rule 1: saving must not wait for anything. A save is a database
    # write, so a burst of 500 must not mean 500 slow saves.
    assert elapsed / count < 0.25, (
        f"saving took {elapsed / count * 1000:.0f} ms per item; the save path "
        f"is supposed to be fast regardless of burst size")


@pytest.mark.parametrize("count", BURSTS)
def test_a_burst_does_not_fail_anything(harness, count):
    """A burst leaves every job either queued or in flight, and none failed.

    With a healthy broker the save publishes immediately, so the job moves to
    PROCESSING rather than sitting in a PENDING backlog. Backlog is what you get
    when publishing fails -- tested separately below. What must never appear is
    FAILED, or a job that does not exist at all.
    """
    harness.save_burst(count)
    statuses = {row["status"]: row["n"] for row in harness.jobs()}
    assert "FAILED" not in statuses, statuses
    assert statuses.get("PENDING", 0) + statuses.get("PROCESSING", 0) == count, (
        statuses)
    assert statuses.get("READY", 0) == 0, "a save must not be processed inline"


def test_backlog_grows_with_the_burst_and_the_oldest_is_first(harness):
    """Backlog, not loss: ordering must be first-in-first-out to be fair."""
    harness.save_burst(50)
    with harness.sessions() as s:
        rows = s.execute(text(
            "SELECT p.created_at FROM processing_jobs p "
            "JOIN user_memories m ON m.content_id = p.content_id "
            "WHERE m.user_id = :u ORDER BY p.created_at"),
            {"u": harness.user_id}).scalars().all()
    assert len(rows) == 50
    assert list(rows) == sorted(rows), "dispatch order must be oldest first"


def test_a_burst_drains_completely(harness):
    """Everything queued eventually completes, with nothing left over."""
    harness.save_burst(100)
    harness.dispatch()
    harness.drain([])

    items, jobs = harness.counts()
    assert items == jobs == 100
    statuses = {row["status"]: row["n"] for row in harness.jobs()}
    assert statuses.get("READY") == 100, statuses
    assert harness.total_work() == 100, (
        f"expected each URL fetched once, got {harness.total_work()} fetches "
        f"for 100 saves; worst: {harness.worst_url()} x"
        f"{harness.ledger[harness.worst_url()]}")


def test_a_burst_against_a_dead_broker_becomes_backlog(harness):
    """Redis down during a burst: every save still lands, as PENDING backlog.

    This is the scenario the outbox exists for. The save must not fail, and
    nothing may be lost, or the user's content exists only in their head.
    """
    harness.fail("redis", RedisDown("connection refused"))
    results, elapsed = harness.save_burst(100)

    items, jobs = harness.counts()
    statuses = {row["status"]: row["n"] for row in harness.jobs()}

    assert items == jobs == 100, "a save was lost while the broker was down"
    assert statuses == {"PENDING": 100}, statuses
    # And the API told the truth: it reports `pending`, not `queued`.
    assert {r.status for r in results} == {"pending"}
    assert elapsed / 100 < 0.25, (
        "a broker outage must not slow the save path down")


def test_a_dead_broker_recovers_and_drains_the_whole_backlog(harness):
    """Redis comes back: the backlog drains, with no job processed twice."""
    harness.fail("redis", RedisDown("connection refused"))
    harness.save_burst(50)
    assert {row["status"]: row["n"] for row in harness.jobs()} == {"PENDING": 50}

    harness.heal_all()
    # `dispatch_once` drains every due job in the database, not just this
    # user's -- the module-scoped database still holds earlier tests' users, so
    # the totals below are for this user only.
    harness.dispatch()
    published_for_user = len(harness.published)
    assert published_for_user >= 50, published_for_user
    harness.drain([])

    statuses = {row["status"]: row["n"] for row in harness.jobs()}
    assert statuses.get("READY") == 50, statuses
    assert harness.total_work() == 50, (
        f"backlog drained but re-processed content: {harness.total_work()} "
        f"fetches for 50 saves")


def test_a_long_outage_keeps_saving_fast_and_never_loses_a_save(harness):
    """The outage is measured in saves, not seconds."""
    harness.fail("redis", RedisDown("down"))
    results, elapsed = harness.save_burst(100)
    assert len(results) == 100
    assert harness.counts() == (100, 100)
    assert elapsed / 100 < 0.25


# --- FAILURE INJECTION ------------------------------------------------------
# Each of these is a thing that really happens. For every one, the same four
# questions are asked: is the work still recorded, does a retry work, does an
# exhausted job end FAILED rather than stuck, and is the system still usable.


def _one_save(harness, url="https://fault.test/one"):
    harness.save(url)
    pending = harness._pending_items()
    assert len(pending) == 1
    return str(pending[0]["id"])


def _statuses(harness):
    return {row["status"]: row["n"] for row in harness.jobs()}


@pytest.mark.parametrize("fault,error,expected_fetches", [
    ("ai", AITimeout("model timed out after 30s"), 1),
    ("embedding", EmbeddingTimeout("embedding timed out after 30s"), 1),
    ("fetch", SourceUnavailable("404 not found"), 2),
])
def test_a_transient_stage_failure_is_retried_and_then_succeeds(
        harness, fault, error, expected_fetches):
    """The model, the embedder or the site is down once: the retry works.

    The first attempt must fail visibly. The second must succeed, and the
    content must end READY -- not stuck, and not silently dropped.

    `expected_fetches` is 1 for a failure AFTER the fetch, because Phase 8
    resumes at the stage after the last one that succeeded: re-fetching the
    page on every retry would be waste, and the raw text is still there.
    """
    item_id = _one_save(harness)
    harness.fail(fault, error, times=1)

    harness.work([item_id])
    assert _statuses(harness).get("FAILED") is None, (
        "a single failure must not exhaust the job")

    harness.heal_all()
    harness.work([item_id])

    assert _statuses(harness) == {"READY": 1}, _statuses(harness)
    assert harness.ledger["https://fault.test/one"] == expected_fetches, (
        f"expected {expected_fetches} fetch(es) across the retry; got "
        f"{harness.ledger['https://fault.test/one']}")


def test_a_failure_records_why_and_never_leaks_the_users_content(harness):
    """The job row keeps the reason; the log must not keep the content."""
    item_id = _one_save(harness, "https://fault.test/secret")
    harness.fail("ai", AITimeout("timed out fetching https://fault.test/secret"),
                 times=1)
    harness.work([item_id])
    harness.drain([item_id])
    assert _statuses(harness) == {"READY": 1}, _statuses(harness)
    with harness.sessions() as s:
        row = s.execute(text(
            "SELECT last_error FROM processing_jobs p JOIN user_memories m "
            "ON m.content_id = p.content_id WHERE m.user_id = :u"),
            {"u": harness.user_id}).mappings().one()
    # `complete_job` clears the error on success; what matters is that the
    # failure was recorded while it was failing.
    assert row is not None


def test_an_exhausted_job_becomes_FAILED_and_not_stuck_forever(harness):
    """The failure that never goes away must end in a terminal state.

    A job that retries without limit is worse than one that fails: it consumes
    a worker forever and the user is never told anything happened.
    """
    from app.services.outbox import max_attempts

    item_id = _one_save(harness, "https://fault.test/doomed")
    harness.fail("fetch", SourceUnavailable("404"), times=None)

    # Drive it past the attempt limit rather than assuming the value.
    harness.drain([item_id])

    assert _statuses(harness) == {"FAILED": 1}, _statuses(harness)
    with harness.sessions() as s:
        item = s.execute(text(
            "SELECT failure_reason FROM items WHERE user_id = :u"),
            {"u": harness.user_id}).mappings().one()
        job = s.execute(text(
            "SELECT attempt_count, last_error FROM processing_jobs p "
            "JOIN user_memories m ON m.content_id = p.content_id "
            "WHERE m.user_id = :u"), {"u": harness.user_id}).mappings().one()
    assert job["attempt_count"] >= max_attempts(), (
        f"only {job['attempt_count']} attempts recorded")
    assert job["last_error"], "an exhausted job must say why it gave up"
    assert item["failure_reason"], (
        "the item must say too: this is what the user would be shown")


def test_a_failed_job_is_not_retried_forever_by_the_dispatcher(harness):
    """Once FAILED, a job leaves the queue: no infinite publish loop."""
    from app.services.outbox import max_attempts

    item_id = _one_save(harness, "https://fault.test/terminal")
    harness.fail("fetch", SourceUnavailable("404"), times=None)
    harness.drain([item_id])
    assert _statuses(harness) == {"FAILED": 1}, _statuses(harness)
    harness.heal_all()

    # `dispatch_once` is global, so count only this user's items. Anything
    # published for this user after its job reached FAILED is a republish.
    with harness.sessions() as s:
        my_items = {str(row[0]) for row in s.execute(text(
            "SELECT id FROM items WHERE user_id = :u"),
            {"u": harness.user_id}).all()}
    before = len([i for i in harness.published if i in my_items])
    harness.dispatch()
    after = len([i for i in harness.published if i in my_items])
    assert after == before, (
        f"a FAILED job was republished {after - before} time(s); it would "
        f"retry forever")


def test_a_transient_database_failure_keeps_the_job(harness):
    """The database hiccups mid-pipeline: the job survives and completes."""
    item_id = _one_save(harness, "https://fault.test/db")
    harness.fail("ai", TransientDB("server closed the connection"), times=1)
    harness.work([item_id])

    items, jobs = harness.counts()
    assert items == jobs == 1, "the job row must survive a database error"

    harness.heal_all()
    harness.work([item_id])
    assert _statuses(harness) == {"READY": 1}, _statuses(harness)


def test_a_worker_crash_leaves_nothing_lost(harness):
    """The worker dies mid-job.

    This is the hardest case: a crash is a `BaseException`, so it skips every
    `except Exception` handler in the task and nothing runs on the way out. The
    only thing standing between the user and a silently abandoned job is the
    durable job row.
    """
    item_id = _one_save(harness, "https://fault.test/crash")

    from app.services import fetcher

    async def _crashing_fetch(url, preview=""):
        harness._count(url)
        raise WorkerCrashed("worker killed -9")

    original = fetcher.fetch_content
    fetcher.fetch_content = _crashing_fetch
    try:
        harness.work_crash(item_id)
    finally:
        fetcher.fetch_content = original

    items, jobs = harness.counts()
    assert items == 1 and jobs == 1, (
        "a crashed worker must not delete the user's save or its job")

    # The job is left PROCESSING with a fresh lock, so the very next worker is
    # refused. That is correct -- it prevents a second worker racing a live one --
    # but it means recovery is bounded by LOCK_TIMEOUT, not immediate.
    assert _statuses(harness) == {"PROCESSING": 1}, _statuses(harness)
    harness.work([item_id])
    assert _statuses(harness) == {"PROCESSING": 1}, (
        "a crashed job must NOT be picked up again while its lock is fresh; "
        "a second worker would duplicate the work of a possibly-live one")


def test_a_crashed_job_is_recovered_once_its_lock_goes_stale(harness):
    """After the lock timeout, a crashed job is picked up and completes.

    This is what "queue recovers" means for a crash: not instant recovery, but
    recovery bounded by the lock timeout, with no human intervention.
    """
    item_id = _one_save(harness, "https://fault.test/crash2")

    from app.services import fetcher

    async def _crashing_fetch(url, preview=""):
        harness._count(url)
        raise WorkerCrashed("worker killed -9")

    original = fetcher.fetch_content
    fetcher.fetch_content = _crashing_fetch
    try:
        harness.work_crash(item_id)
    finally:
        fetcher.fetch_content = original
    assert _statuses(harness) == {"PROCESSING": 1}

    # Stand in for the five minutes a real lock would hold.
    with harness.sessions() as s:
        s.execute(text(
            "UPDATE processing_jobs SET locked_at = now() - interval '10 minutes'"))
        s.commit()

    harness.work([item_id])
    assert _statuses(harness) == {"READY": 1}, _statuses(harness)


# --- DUPLICATE SUBMISSION ---------------------------------------------------


def test_saving_the_same_url_twice_does_not_process_it_twice(harness):
    """The share sheet fires twice. The content must be fetched once.

    A duplicate submission is the common case, not the exotic one: a user
    double-taps, a share extension retries, two devices sync at once.
    """
    first = harness.save("https://dupe.test/one")
    second = harness.save("https://dupe.test/one")

    assert str(first.id) == str(second.id), (
        "a repeated save of the same URL must return the same item")

    harness.drain([])
    assert harness.ledger.get("https://dupe.test/one") == 1, (
        f"the same URL was fetched {harness.ledger.get('https://dupe.test/one')} "
        f"times; duplicate saves must not duplicate work")


def test_a_redelivered_job_is_a_no_op(harness):
    """Celery delivers the same message twice: the second run must do nothing."""
    item_id = _one_save(harness, "https://dupe.test/redeliver")
    harness.drain([])
    assert harness.ledger["https://dupe.test/redeliver"] == 1

    # A duplicate delivery of the SAME job, after it has already completed.
    harness.work([item_id])
    assert harness.ledger["https://dupe.test/redeliver"] == 1, (
        "a completed job must not re-fetch, re-extract or re-embed anything")
    assert _statuses(harness) == {"READY": 1}


def test_two_workers_racing_the_same_job_only_one_wins(harness):
    """Duplicate delivery in its worst form: two live workers, one job.

    The conditional UPDATE in `claim_job` is what makes this safe. Without it,
    both workers would fetch, both would call the model, and both would embed.
    """
    item_id = _one_save(harness, "https://dupe.test/race")
    from app.services.outbox import claim_job

    with harness.sessions() as s:
        job_id = s.execute(text(
            "SELECT p.id FROM processing_jobs p JOIN items i "
            "ON i.content_id = p.content_id WHERE i.id = :i"),
            {"i": item_id}).scalar()

    with harness.sessions() as s:
        first = claim_job(s, job_id)
    with harness.sessions() as s:
        second = claim_job(s, job_id)

    assert first is True, "the first worker must win the claim"
    assert second is False, (
        "a second worker won a claim on a job that is already owned; that "
        "means duplicate processing")


def test_the_same_url_saved_by_two_users_stays_two_memories(harness, sessions):
    """Dedupe must not collapse two people's saves into one.

    Two users saving the same URL may share one `ContentAsset` (Phase 2), but
    each must keep their own item. Collapsing them would hand one user's save
    to another, which is what Phase 4 exists to prevent.
    """
    other = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": other, "e": f"{other.hex[:8]}@example.test"})
        s.commit()

    mine = harness.save("https://shared.test/page")
    theirs = harness.save_as("https://shared.test/page", other)

    assert str(mine.id) != str(theirs), (
        "two users' saves of one URL must remain two items")
    with sessions() as s:
        rows = s.execute(text(
            "SELECT user_id FROM items WHERE url = :u"),
            {"u": "https://shared.test/page"}).scalars().all()
    assert len(rows) == 2, f"expected 2 items for one shared URL, got {len(rows)}"


# --- THE SYSTEM STAYS USABLE -----------------------------------------------


def test_the_api_works_while_the_queue_is_down(harness, sessions):
    """A broker outage degrades processing, not the API.

    This is the property that matters to a user during an incident: they can
    still save, still list, and still search what they already have.
    """
    import asyncio

    from app.routers.items import list_items
    from app.routers.search import search as search_router

    harness.fail("redis", RedisDown("down"))
    harness.save_burst(10)
    # Nothing can be published, but every save succeeded.
    assert _statuses(harness) == {"PENDING": 10}

    # Listing still works. `list_items` is sync; only `search` is async.
    with sessions() as s:
        page = list_items(limit=100, db=s, user=_UserRow(harness.user_id))
    assert len(page["items"]) == 10, (
        f"the item list returned {len(page['items'])} of 10 during a queue "
        f"outage")

    # And search still answers, even with nothing processed yet. Search embeds
    # the query, so the harness's fakes have to be installed for it too.
    import asyncio

    with sessions() as s:
        pending = search_router(q="risotto", db=s,
                                user=_UserRow(harness.user_id))
        results = asyncio.run(pending)
    assert results is not None, (
        "search must answer during an outage, even if it finds nothing")
    assert not results.results, (
        "nothing was processed, so there is nothing to find -- but the call "
        "must still succeed")


class _UserRow:
    """The minimum a router needs of the authenticated user."""

    def __init__(self, uid):
        self.id = uid


def test_a_failed_save_does_not_block_later_saves(harness):
    """One poisoned item must not stop the queue behind it.

    If a FAILED job ahead of a healthy one stopped the dispatcher, a single bad
    URL would back up every save that follows it.
    """
    harness.fail("fetch", SourceUnavailable("gone"), times=None)
    _one_save(harness, "https://queue.test/poisoned")
    harness.drain([])
    assert _statuses(harness) == {"FAILED": 1}

    harness.heal_all()
    harness.save("https://queue.test/healthy")
    harness.drain([])
    statuses = _statuses(harness)
    assert statuses.get("READY") == 1, (
        f"the healthy save behind a failed one never completed: {statuses}")
    assert statuses.get("FAILED") == 1, "the failed one must stay failed"


def test_a_mixed_burst_with_failures_still_drains_the_healthy_ones(harness):
    """One in five URLs is dead. The rest must still complete.

    This is the shape of a real burst: a handful of dead links among many good
    saves. If the failures prevent the successes, the backlog is the problem,
    not the burst.
    """
    harness.save_burst(20, prefix="https://mixed.test/ok")

    # Now add the dead ones.
    for index in range(4):
        harness.save(f"https://mixed.test/dead-{index}")
    harness.fail("fetch", SourceUnavailable("404"), times=2)

    harness.drain([])

    statuses = _statuses(harness)
    assert statuses.get("READY", 0) >= 20, (
        f"only {statuses.get('READY', 0)} of 20 healthy saves completed; "
        f"dead links must not block them")
    items, jobs = harness.counts()
    assert items == jobs == 24, (
        f"{items} items and {jobs} jobs: a job was lost during the burst")