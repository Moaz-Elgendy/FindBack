"""Phase 18: observability -- metrics increment, logs carry fields, no private text.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase18_observability.py -q

Two claims under test, and both are checked against the real thing rather than
against a mock of it:

    every metric of the phase increments by the right amount
    every structured event carries the phase's fields and no user content

The privacy half is the one that matters most, so it is stated as a property
rather than as examples: a value the phase treats as private is planted, the
code path is driven, and the assertion is that the value appears NOWHERE in the
captured log output. `app/services/privacy.py` redacts registered values;
`observability` refuses unrecognised fields outright. Both are exercised.

Metrics are asserted through the rendered Prometheus text, not through the
Python attributes, because the text is what a scraper reads: a counter that
increments correctly but renders wrong is still a broken metric.

The privacy half has to be proven in the WORKER, not only in the API. Fetching,
the model call and embedding all run there, so that is the process holding the
raw content -- and a Celery worker never imports `app.main`, so it installs the
filters itself (`app/celery_app.py`). The tests below do the same, and then
drive a pipeline whose stages fail with the private text in hand.
"""
import asyncio
import json
import os
import uuid

import pytest

from app import database as db_module
from app.services import metrics, observability

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 18 database tests",
)

# The names the phase requires, exactly as spelled in its metric list.
REQUIRED_METRICS = (
    "captures_total",
    "processing_jobs_total",
    "processing_success_total",
    "processing_failures_total",
    "processing_duration",
    "queue_depth",
    "queue_wait_time",
    "dedupe_hits",
    "dedupe_misses",
    "ai_requests",
    "ai_failures",
    "embedding_requests",
    "search_requests",
    "search_latency",
)

REQUIRED_LOG_FIELDS = (
    "request_id", "user_id", "content_id", "job_id",
    "pipeline_version", "stage", "status", "duration_ms",
)

# A sentence that must never reach a log. Long enough that the privacy filter's
# prose heuristic would catch it even if it were registered, and specific enough
# that finding it anywhere is unambiguous.
PRIVATE_PAGE = (
    "The Northwind procurement runbook explains how to rotate the shared "
    "database credentials without downtime, including the exact vault paths."
)
PRIVATE_NOTE = "my secret note about the landlord's phone number"
PRIVATE_QUERY = "the video where they explain rotating vault credentials"


# --- reading the output ---------------------------------------------------

def _value(rendered: str, name: str) -> float:
    """The total of a metric across its label combinations.

    A counter may be split by `outcome`, and summing is what an operator means
    by "how many dedupe hits are there". A histogram's `_sum` is never read
    through here; `_histogram` handles those.
    """
    total = 0.0
    for line in rendered.splitlines():
        if line.startswith("#"):
            continue
        head, _, tail = line.rpartition(" ")
        if head == f"findback_{name}" or (
                head.startswith(f"findback_{name}{{")
                and head.endswith("}")):
            total += float(tail)
    return total


def _samples(rendered: str, name: str) -> dict:
    """Every series of a metric, keyed by its label text."""
    out = {}
    for line in rendered.splitlines():
        if line.startswith("#"):
            continue
        head, _, tail = line.rpartition(" ")
        if head.startswith(f"findback_{name}"):
            out[head[len(f"findback_{name}"):]] = float(tail)
    return out


def _histogram(rendered: str, name: str) -> dict:
    samples = _samples(rendered, name)
    return {
        "buckets": {k: v for k, v in samples.items() if k.startswith("_bucket")},
        "sum": samples.get("_sum", 0.0),
        "count": samples.get("_count", 0.0),
    }


# --- capturing what the code actually logged ------------------------------

class _Captured:
    """Collect records from one logger, independent of the root logger.

    `caplog` hangs off the root logger, which makes an assertion depend on
    whatever any other test did to the logging tree -- and Phase 16 found that
    a migration can disable a logger outright. A handler on the logger under
    test is unambiguous.
    """

    def __init__(self, logger_name, level=0):
        self.logger_name = logger_name
        self.level = level
        self.records = []

    def __enter__(self):
        import logging

        log = logging.getLogger(self.logger_name)
        outer = self

        class _Collect(logging.Handler):
            def emit(self, record):
                outer.records.append(record.getMessage())

        self._handler = _Collect(level=self.level)
        self._previous = log.level
        log.addHandler(self._handler)
        log.setLevel(self.level)
        return self

    def __exit__(self, *exc_info):
        import logging

        log = logging.getLogger(self.logger_name)
        log.removeHandler(self._handler)
        log.setLevel(self._previous)
        return False

    def events(self) -> list:
        """The structured payloads, parsed. Non-JSON lines are skipped."""
        parsed = []
        for message in self.records:
            text = message.strip()
            if not text.startswith("{"):
                continue
            try:
                parsed.append(json.loads(text))
            except json.JSONDecodeError:
                continue
        return parsed

    def named(self, event: str) -> list:
        return [e for e in self.events() if e.get("event") == event]

    def text(self) -> str:
        return "\n".join(self.records)


@pytest.fixture(autouse=True)
def clean_metrics():
    """Every test starts from a zeroed registry and a zeroed privacy set."""
    from app.services import privacy

    metrics.reset()
    privacy.reset_private()
    yield
    metrics.reset()
    privacy.reset_private()


@pytest.fixture(autouse=True)
def isolated_database_module():
    """Do not leave a database module state behind.

    `/metrics` and `/health` call `SessionLocal()`, which caches a module-level
    engine and sessionmaker bound to `DATABASE_URL`. A later test that repoints
    `_engine` at its own throwaway database would then find a stale cache, and
    its assertions would be about rows nothing wrote. `get_sessionmaker` now
    rebuilds on an engine change, so this is belt and braces -- but leaving
    global state dirty is how this suite got into trouble in the first place.
    """
    saved_engine = db_module._engine
    saved_sessionmaker = db_module._sessionmaker
    yield
    db_module._engine = saved_engine
    db_module._sessionmaker = saved_sessionmaker


@pytest.fixture(scope="module")
def admin_engine():
    eng = db_module.create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture
def db(admin_engine):
    """A throwaway database at head, built by the real Alembic chain."""
    name = f"fb_p18_{uuid.uuid4().hex[:10]}"
    admin = db_module.create_engine(
        TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
        isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(db_module.text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = db_module.create_engine(url, pool_pre_ping=True)
    try:
        from alembic import command

        cfg = db_module.alembic_config()
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(
                db_module.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def sessions(db):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=db)


def _fresh_database(prefix):
    """A named throwaway database, as (admin, name, url)."""
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = db_module.create_engine(
        TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
        isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(db_module.text(f'CREATE DATABASE "{name}"'))
    return admin, name, TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"


def _migrated(url):
    from alembic import command

    cfg = db_module.alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return db_module.create_engine(url, pool_pre_ping=True)


def _drop(admin, name, engine=None):
    if engine is not None:
        engine.dispose()
    with admin.connect() as conn:
        conn.execute(db_module.text(
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _ago(seconds):
    """A timestamp `seconds` in the past, or None when there is not one.

    Bound as a real datetime so the column is genuinely NULL for a job nobody
    has claimed, rather than "now", which would make its wait time a zero that
    looks like a real measurement.
    """
    import datetime

    if seconds is None:
        return None
    return datetime.datetime.now(datetime.timezone.utc) - \
        datetime.timedelta(seconds=seconds)


# --- the registry itself ---------------------------------------------------

def test_a_sessionmaker_follows_the_engine_it_was_replaced_with():
    """A defect this phase ran into, kept as a regression test.

    `/metrics` and `/health` call `SessionLocal()`, which caches a sessionmaker
    bound to `DATABASE_URL`. Several tests repoint `db_module._engine` at a
    throwaway database and expect `SessionLocal()` to follow. With an
    unconditional cache the worker's own SQL went to whichever database was
    current when the cache was first built, so the test asserted on rows nothing
    had written to it -- `test_phase5_outbox::test_outage_does_not_stuck_a_save`
    failed with `assert 'pending' == 'ready'` for exactly that reason.
    """
    import sqlalchemy

    first_engine = db_module.get_engine()
    first_maker = db_module.get_sessionmaker()
    assert first_maker.kw.get("bind") is first_engine
    # Still cached when nothing has changed.
    assert db_module.get_sessionmaker() is first_maker

    second_engine = sqlalchemy.create_engine(
        TEST_DATABASE_URL or "postgresql://localhost/none", pool_pre_ping=True)
    saved = db_module._engine
    db_module._engine = second_engine
    try:
        second_maker = db_module.get_sessionmaker()
        assert second_maker.kw.get("bind") is second_engine, (
            "the cached sessionmaker is still bound to the old engine")
        session = db_module.SessionLocal()
        try:
            assert session.get_bind() is second_engine
        finally:
            session.close()
    finally:
        db_module._engine = saved
        second_engine.dispose()


def test_every_required_metric_exists_and_is_documented():
    rendered = metrics.render()
    for name in REQUIRED_METRICS:
        assert f"# HELP findback_{name} " in rendered, (
            f"{name} has no HELP line")
        assert f"# TYPE findback_{name} " in rendered, (
            f"{name} has no TYPE line")


def test_the_phase_metrics_are_present_at_the_metrics_endpoint():
    """Over HTTP, because that is how a scraper reaches them."""
    import httpx

    from app.main import app

    async def get_metrics():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            return await client.get("/metrics")

    response = asyncio.run(get_metrics())
    assert response.status_code == 200, response.text
    assert "text/plain" in response.headers.get("content-type", ""), (
        response.headers.get("content-type"))
    for name in REQUIRED_METRICS:
        assert f"findback_{name}" in response.text, (
            f"/metrics does not expose {name}")


# --- counters --------------------------------------------------------------

def test_captures_total_counts_each_save():
    for _ in range(3):
        metrics.captures_total.inc()
    assert _value(metrics.render(), "captures_total") == 3.0


def test_dedupe_hits_and_misses_are_counted_separately():
    """A hit means the save joined existing content; a miss created an asset.
    The ratio is what tells you whether caching is working."""
    metrics.dedupe_hits.inc(labels={"outcome": "key"})
    metrics.dedupe_hits.inc(labels={"outcome": "url"})
    metrics.dedupe_hits.inc(labels={"outcome": "item"})
    metrics.dedupe_misses.inc(labels={"outcome": "asset"})

    rendered = metrics.render()
    assert _value(rendered, "dedupe_hits") == 3.0
    assert _value(rendered, "dedupe_misses") == 1.0
    assert '{outcome="key"}' in rendered
    assert '{outcome="asset"}' in rendered


def test_a_counter_refuses_to_go_backwards():
    with pytest.raises(ValueError):
        metrics.captures_total.inc(-1)


def test_a_counter_with_the_wrong_labels_is_refused():
    """Silently accepting an unexpected label would split one metric into two
    series that look like different things."""
    with pytest.raises(ValueError):
        metrics.dedupe_hits.inc(labels={"outcome": "key", "user": "a"})


def test_ai_and_embedding_requests_are_counted_at_the_gateway():
    """Counted where the call leaves the app, so a provider swap does not
    change the number and a test-installed fake is counted too."""
    import asyncio as _asyncio

    from app.services import ai_gateway

    calls = []

    class _Adapter:
        name = "test"

        async def generate_json(self, system_prompt, user_prompt, *,
                                temperature=0.0):
            calls.append("chat")
            return {"ok": True}

        async def embed(self, texts, *, task="document"):
            calls.append("embed")
            return [[0.0] * 4 for _ in texts]

        async def embed_one(self, text, *, task="document"):
            calls.append("embed_one")
            return [0.0] * 4

        def model_name(self):
            return "test-model"

    previous = ai_gateway.set_gateway(ai_gateway.Gateway(adapter=_Adapter()))
    try:
        _asyncio.run(ai_gateway.get_gateway().generate_json("s", "u"))
        _asyncio.run(ai_gateway.get_gateway().generate_json("s", "u"))
        _asyncio.run(ai_gateway.get_gateway().embed(["a", "b"]))
        _asyncio.run(ai_gateway.get_gateway().embed_one("c"))
    finally:
        ai_gateway.set_gateway(previous)

    rendered = metrics.render()
    assert _value(rendered, "ai_requests") == 2.0, calls
    assert _value(rendered, "embedding_requests") == 2.0, calls
    assert _value(rendered, "ai_failures") == 0.0


def test_a_failed_provider_call_counts_a_failure_and_still_raises():
    """The metric must not swallow the exception: ingest depends on the raise
    to keep its own error handling honest."""
    import asyncio as _asyncio

    from app.services import ai_gateway

    class _Broken:
        name = "broken"

        async def generate_json(self, *args, **kwargs):
            raise RuntimeError("provider said no")

        async def embed(self, texts, *, task="document"):
            raise RuntimeError("provider said no")

        async def embed_one(self, text, *, task="document"):
            raise RuntimeError("provider said no")

        def model_name(self):
            return "broken"

    previous = ai_gateway.set_gateway(ai_gateway.Gateway(adapter=_Broken()))
    try:
        with pytest.raises(RuntimeError):
            _asyncio.run(ai_gateway.get_gateway().generate_json("s", "u"))
    finally:
        ai_gateway.set_gateway(previous)

    rendered = metrics.render()
    assert _value(rendered, "ai_requests") == 1.0
    assert _value(rendered, "ai_failures") == 1.0


def test_a_provider_error_message_never_reaches_the_log():
    """The message is where a provider quotes the request that failed, and the
    request is the user's content."""
    import asyncio as _asyncio

    from app.services import ai_gateway

    class _Leaky:
        name = "leaky"

        async def generate_json(self, system_prompt, user_prompt, *,
                                temperature=0.0):
            raise RuntimeError(f"upstream rejected: {PRIVATE_PAGE}")

        async def embed(self, texts, *, task="document"):
            raise RuntimeError(f"upstream rejected: {PRIVATE_PAGE}")

        async def embed_one(self, text, *, task="document"):
            raise RuntimeError(f"upstream rejected: {PRIVATE_PAGE}")

        def model_name(self):
            return "leaky"

    previous = ai_gateway.set_gateway(ai_gateway.Gateway(adapter=_Leaky()))
    try:
        with _Captured("findback.gateway",
                       level=10) as captured, \
                _Captured("findback.observability", level=10) as events:
            with pytest.raises(RuntimeError):
                _asyncio.run(ai_gateway.get_gateway().generate_json("s", PRIVATE_PAGE))
            logged = captured.text() + events.text()
        assert PRIVATE_PAGE not in logged, (
            f"the provider error text reached the log: {logged[:400]}")
    finally:
        ai_gateway.set_gateway(previous)


# --- histograms ------------------------------------------------------------

def test_processing_duration_buckets_an_observation_once():
    metrics.processing_duration.observe(0.2)
    histogram = _histogram(metrics.render(), "processing_duration")
    assert histogram["count"] == 1.0, histogram
    # +Inf always equals the number of observations, whatever the value was.
    assert histogram['buckets']['_bucket{le="+Inf"}'] == 1.0
    assert histogram['buckets']['_bucket{le="0.5"}'] == 1.0
    assert histogram['buckets']['_bucket{le="300"}'] == 1.0
    assert abs(histogram["sum"] - 0.2) < 1e-3


def test_a_bucket_boundary_is_inclusive():
    metrics.processing_duration.observe(2.5)
    histogram = _histogram(metrics.render(), "processing_duration")
    assert histogram['buckets']['_bucket{le="2.5"}'] == 1.0
    assert histogram['buckets']['_bucket{le="1"}'] == 0.0


def test_a_histogram_does_not_count_one_observation_once_per_bucket():
    """The bug that a naive implementation has: one sample lands in every
    bucket it is <=, so summing the buckets reports 9 observations for 1."""
    metrics.search_latency.observe(0.001)
    histogram = _histogram(metrics.render(), "search_latency")
    assert histogram["count"] == 1.0, histogram
    assert histogram['buckets']['_bucket{le="+Inf"}'] == 1.0


def test_bucket_boundaries_are_inclusive():
    metrics.processing_duration.observe(1.0)
    histogram = _histogram(metrics.render(), "processing_duration")
    assert histogram['buckets']['_bucket{le="1"}'] == 1.0
    assert histogram['buckets']['_bucket{le="0.5"}'] == 0.0


def test_several_observations_land_in_the_right_buckets():
    """0.2, 0.7 and 3.0 are all under 5s; 400 is not. Cumulative buckets are
    the count at or below each bound, which is the easy thing to get wrong
    twice -- once by summing them, once by treating them as per-bucket."""
    for value in (0.2, 0.7, 3.0, 400.0):
        metrics.processing_duration.observe(value)
    histogram = _histogram(metrics.render(), "processing_duration")
    assert histogram["count"] == 4.0
    assert histogram['buckets']['_bucket{le="0.5"}'] == 1.0
    assert histogram['buckets']['_bucket{le="1"}'] == 2.0
    assert histogram['buckets']['_bucket{le="2.5"}'] == 2.0
    assert histogram['buckets']['_bucket{le="5"}'] == 3.0
    # 400 is past the last finite bound, so it lands only in +Inf.
    assert histogram['buckets']['_bucket{le="300"}'] == 3.0
    assert histogram['buckets']['_bucket{le="+Inf"}'] == 4.0
    assert abs(histogram["sum"] - 403.9) < 1e-3


def test_the_timer_measures_and_reports_the_same_number():
    with metrics.Timer(metrics.search_latency) as timer:
        pass
    histogram = _histogram(metrics.render(), "search_latency")
    assert histogram["count"] == 1.0
    # `elapsed_ms` is what a caller logs; it must be the same measurement the
    # histogram recorded, not a second reading. The tolerance is the text
    # format's own rounding to six decimal places.
    assert abs(histogram["sum"] - timer.seconds) < 1e-6
    assert timer.elapsed_ms == int(round(timer.seconds * 1000))


def test_observe_ms_converts_milliseconds_to_seconds():
    metrics.observe_ms(metrics.search_latency, 250)
    histogram = _histogram(metrics.render(), "search_latency")
    assert histogram["count"] == 1.0
    assert abs(histogram["sum"] - 0.25) < 1e-9


# --- the queue, derived from the database ---------------------------------

@needs_db
def test_queue_metrics_are_derived_from_the_jobs_table():
    """A Celery worker is a different process, so a counter incremented there
    could never appear in the API's /metrics. These come from the rows."""
    from sqlalchemy import text

    admin, name, url = _fresh_database("fb_p18_queue")
    engine = None
    try:
        engine = _migrated(url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('q@e.test')"))
            # One asset per job: the Phase 6 partial unique index allows only
            # one ACTIVE job per (content_id, job_type), so five jobs on one
            # asset would be rejected before the metric was ever read.
            for index, (status, waited, ran) in enumerate((
                ("READY", 1.0, 4.0),
                ("READY", 2.0, 6.0),
                ("FAILED", 0.5, 1.5),
                # A job nobody has claimed yet has NO claimed_at, which is what
                # keeps it out of both duration histograms.
                ("PENDING", None, None),
                ("PENDING", None, None),
            )):
                url_ = f"https://example.test/q{index}"
                conn.execute(text("""
                    INSERT INTO content_assets (canonical_url, visibility,
                                                 owner_user_id)
                    SELECT :url, 'UNKNOWN', id FROM users
                     WHERE email = 'q@e.test'
                """), {"url": url_})
                # make_interval needs a real double, so the intervals are built
                # in Python and bound as NULL where the job was never claimed.
                conn.execute(text("""
                    INSERT INTO processing_jobs (content_id, job_type, status,
                                                 created_at, claimed_at,
                                                 updated_at)
                    SELECT a.id, 'process_item:v1', :status,
                           now() - make_interval(secs => :created_back),
                           :claimed_at, now()
                      FROM content_assets a WHERE a.canonical_url = :url
                """), {"status": status, "url": url_,
                       "created_back": (waited + ran) if ran is not None else 0.0,
                       "claimed_at": _ago(ran)})

        with engine.connect() as conn:
            summary = metrics.collect_queue_metrics(conn)

        assert summary["ok"], summary
        assert summary["counts"]["total"] == 5, summary
        assert summary["counts"]["ready"] == 2, summary
        assert summary["counts"]["failed"] == 1, summary
        assert summary["counts"]["pending"] == 2, summary

        rendered = metrics.render()
        assert _value(rendered, "processing_jobs_total") == 5.0
        assert _value(rendered, "processing_success_total") == 2.0
        assert _value(rendered, "processing_failures_total") == 1.0
        assert _value(rendered, "queue_depth") == 2.0

        wait = _histogram(rendered, "queue_wait_time")
        assert wait["count"] == 3.0, wait
        assert abs(wait["sum"] - 3.5) < 0.5, wait
        duration = _histogram(rendered, "processing_duration")
        assert duration["count"] == 3.0, duration
        assert abs(duration["sum"] - 11.5) < 0.5, duration
    finally:
        _drop(admin, name, engine)


@needs_db
def test_scraping_the_queue_twice_does_not_double_count():
    """The DB path SETS rather than accumulates, so a scraper that polls every
    15 seconds does not report sixteen times the real backlog."""
    from sqlalchemy import text

    admin, name, url = _fresh_database("fb_p18_idem")
    engine = None
    try:
        engine = _migrated(url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('i@e.test')"))
            conn.execute(text("""
                INSERT INTO content_assets (canonical_url, visibility,
                                             owner_user_id)
                SELECT 'https://example.test/i', 'UNKNOWN', id FROM users
                 WHERE email = 'i@e.test'
            """))
            conn.execute(text("""
                INSERT INTO processing_jobs (content_id, job_type, status,
                                             created_at, claimed_at, updated_at)
                SELECT a.id, 'process_item:v1', 'PENDING', now(), NULL, now()
                  FROM content_assets a
                 WHERE a.canonical_url = 'https://example.test/i'
            """))
        with engine.connect() as conn:
            for _ in range(5):
                metrics.collect_queue_metrics(conn)
        rendered = metrics.render()
        assert _value(rendered, "queue_depth") == 1.0
        assert _value(rendered, "processing_jobs_total") == 1.0
    finally:
        _drop(admin, name, engine)


def test_a_broken_database_does_not_fail_the_scrape():
    """A scrape must not 500. A broken metric source is an observability
    problem, not a reason to report the whole service as down."""
    class _Dead:
        def execute(self, *args, **kwargs):
            raise RuntimeError("the database is on fire")

    metrics.queue_depth.set(9)
    summary = metrics.collect_queue_metrics(_Dead())
    assert summary["ok"] is False
    assert "the database is on fire" not in metrics.render()
    # The last known value survives rather than being zeroed.
    assert _value(metrics.render(), "queue_depth") == 9.0


@needs_db
def test_claiming_a_job_records_its_claim_time():
    """`queue_wait_time` needs a durable stamp, because the worker that would
    otherwise hold it is a different process from the one being scraped."""
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.services.outbox import claim_job

    admin, name, url = _fresh_database("fb_p18_claim")
    engine = None
    try:
        engine = _migrated(url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('c@e.test')"))
            conn.execute(text("""
                INSERT INTO content_assets (canonical_url, visibility,
                                             owner_user_id)
                SELECT 'https://example.test/c', 'UNKNOWN', id FROM users
                 WHERE email = 'c@e.test'
            """))
            conn.execute(text("""
                INSERT INTO processing_jobs (content_id, job_type, status,
                                             created_at)
                SELECT a.id, 'process_item:v1', 'PENDING',
                       now() - interval '30 seconds'
                  FROM content_assets a
                 WHERE a.canonical_url = 'https://example.test/c'
            """))

        Session = sessionmaker(bind=engine)
        with Session() as s:
            job_id = s.execute(text(
                "SELECT id FROM processing_jobs")).scalar()
            assert claim_job(s, job_id) is True
            first = s.execute(text(
                "SELECT claimed_at FROM processing_jobs")).scalar()
            assert first is not None, "claim_job did not stamp claimed_at"

            # A retry keeps the ORIGINAL claim time, so queue_wait_time answers
            # "how long did this wait before anyone started it" rather than
            # "how long since the last attempt".
            s.execute(text("UPDATE processing_jobs SET status='PENDING', "
                           "locked_at=NULL"))
            s.commit()
            assert claim_job(s, job_id) is True
            second = s.execute(text(
                "SELECT claimed_at FROM processing_jobs")).scalar()
            assert second == first, "a retry overwrote the original claim time"

        with engine.connect() as conn:
            metrics.collect_queue_metrics(conn)
        wait = _histogram(metrics.render(), "queue_wait_time")
        assert wait["count"] == 1.0, wait
        assert wait["sum"] >= 29.0, wait
    finally:
        _drop(admin, name, engine)


# --- the structured log fields -------------------------------------------

def test_an_event_carries_every_field_the_phase_names():
    payload = observability.log_event(
        "test.event",
        request_id="req-1", user_id="u-1", content_id="c-1", job_id="j-1",
        pipeline_version="process_item:v1", stage="FETCH", status="created",
        duration_ms=12.7)
    for name in REQUIRED_LOG_FIELDS:
        assert name in payload, f"{name} is missing from {sorted(payload)}"
    assert payload["duration_ms"] == 13, payload
    assert payload["stage"] == "FETCH"
    assert payload["pipeline_version"] == "process_item:v1"


def test_identifiers_are_hashed_but_the_request_id_is_not():
    """Two reasons for two rules. Hashed, so a log index holds nothing that
    identifies a person. Request id in the clear, because an operator has to be
    able to read it back to find the request."""
    payload = observability.prepare("e.f", {
        "request_id": "trace-abc", "user_id": "11111111-1111-1111-1111-111111111111",
        "content_id": "22222222-2222-2222-2222-222222222222",
        "job_id": "33333333-3333-3333-3333-333333333333"})
    assert payload["request_id"] == "trace-abc"
    assert "11111111" not in payload["user_id"]
    assert "22222222" not in payload["content_id"]
    assert "33333333" not in payload["job_id"]
    for name in ("user_id", "content_id", "job_id"):
        assert len(payload[name]) == 12, payload


def test_the_same_identifier_hashes_the_same_way_in_two_events():
    """Otherwise correlation across lines of one request would be impossible,
    which is the whole reason to keep a pseudonymous id at all."""
    a = observability.prepare("e.f", {"user_id": "u-9"})
    b = observability.prepare("e.g", {"user_id": "u-9"})
    assert a["user_id"] == b["user_id"]


def test_an_unknown_field_is_refused_rather_than_dropped():
    """Passing a note is how private content reaches a log. It has to be a
    TypeError, not a silent omission."""
    for field in ("user_note", "user_intent", "url", "title", "summary",
                  "text", "content", "email", "token"):
        with pytest.raises(TypeError):
            observability.prepare("e.f", {field: "anything"})


@pytest.mark.parametrize("value", [
    "a note written in full sentences",
    "https://example.test/a/page",
    "semi;colon and spaces",
    "x" * 65,
    "",
])
def test_free_text_is_refused_in_a_slug_field(value):
    """`status`, `stage` and `pipeline_version` come from closed vocabularies,
    so anything that reads like a sentence did not come from one."""
    for field in ("status", "stage", "pipeline_version"):
        with pytest.raises(ValueError):
            observability.prepare("e.f", {field: value})


def test_a_route_template_is_accepted_because_it_is_not_free_text():
    payload = observability.prepare("e.f", {"stage": "/api/v1/items/{item_id}"})
    assert payload["stage"] == "/api/v1/items/{item_id}"


def test_duration_must_be_a_number():
    payload = observability.prepare("e.f", {"duration_ms": "12.5"})
    assert payload["duration_ms"] == 12, payload


def test_none_valued_fields_are_dropped_rather_than_written_as_null():
    """A field nobody knows yet should be absent, so a query for its presence
    means something."""
    payload = observability.prepare("e.f", {"job_id": None, "stage": "FETCH"})
    assert "job_id" not in payload
    assert payload["stage"] == "FETCH"


def test_the_event_name_must_be_a_dotted_slug():
    for bad in ("", "with space", "with/slash"):
        with pytest.raises(ValueError):
            observability.prepare(bad, {})


def test_an_exception_is_described_without_its_message():
    class ProviderError(Exception):
        pass


    described = observability.describe_exc(ProviderError(PRIVATE_PAGE))
    assert described.startswith("ProviderError(")
    assert PRIVATE_PAGE not in described
    assert "Northwind" not in described
    assert observability.describe_exc(ProviderError("")) == "ProviderError"


def test_a_request_id_from_the_caller_is_kept_but_sanitised():
    assert observability.validate_request_id("trace-1") == "trace-1"
    # A newline would let a caller forge extra log lines.
    assert observability.validate_request_id("a\nb") != "a\nb"
    assert observability.validate_request_id("x" * 400) != "x" * 400
    assert observability.validate_request_id(None)


def test_the_request_id_is_inherited_from_the_context():
    token = observability.bind_request_id("ctx-77")
    try:
        payload = observability.prepare("e.f", {"stage": "FETCH"})
        assert payload["request_id"] == "ctx-77"
    finally:
        observability.reset_request_id(token)
    assert observability.current_request_id() is None


def test_clearing_the_context_does_not_leak_into_the_next_request():
    token = observability.bind_request_id("ctx-88")
    observability.reset_request_id(token)
    assert "request_id" not in observability.prepare("e.f", {})


# --- the privacy rule, as a property --------------------------------------

@needs_db
def test_no_private_content_reaches_a_log_from_the_real_save_path(
        sessions, db):
    """The headline rule, driven through the actual ingest code.

    The page text is registered as private the way the gateway does it, then a
    save that carries it is driven end to end, and the assertion is that not one
    character of it is in the output.
    """
    import app.models as models
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest
    from app.services import privacy

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(db_module.text(
            "INSERT INTO users (id, email) VALUES (:i, :e)"),
            {"i": uid, "e": "p18@example.test"})
        s.commit()

    class _UserRow:
        def __init__(self, uid):
            self.id = uid

    with sessions() as s:
        # What the pipeline would have handed the save.
        privacy.register_private(PRIVATE_PAGE)
        with _Captured("findback.ingest") as ingest_log, \
                _Captured("findback.observability") as events, \
                _Captured("findback.gateway") as gateway_log:
            from app.services.ai_gateway import set_gateway
            from app.services import ai_gateway

            class _Adapter:
                name = "test"

                async def generate_json(self, *a, **k):
                    return {"ok": True}

                async def embed(self, texts, *, task="document"):
                    return [[0.0] * 4 for _ in texts]

                async def embed_one(self, text, *, task="document"):
                    return [0.0] * 4

                def model_name(self):
                    return "test-model"

            previous = set_gateway(ai_gateway.Gateway(adapter=_Adapter()))
            try:
                result = ingest(
                    IngestRequest(url="https://example.test/private-page",
                                  preview=PRIVATE_PAGE),
                    s, _UserRow(uid))
            finally:
                set_gateway(previous)
        logged = ingest_log.text() + events.text() + gateway_log.text()
        s.commit()

    assert result.id is not None
    for secret in (PRIVATE_PAGE, "Northwind", "vault credentials", "rotate"):
        assert secret not in logged, (
            f"{secret!r} reached a log:\n{logged[:800]}")


@needs_db
def test_the_search_query_never_reaches_a_log(sessions, db):
    """A search query is the user recalling something from memory. It is the
    most private string in the system and the easiest to leak by accident."""
    import httpx

    from app.auth import get_current_user
    from app.database import get_db
    from app.main import app
    from app.services import embedder

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(db_module.text(
            "INSERT INTO users (id, email) VALUES (:i, :e)"),
            {"i": uid, "e": "q18@example.test"})
        s.commit()

    previous_engine = db_module._engine
    previous_sessionmaker = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = sessions

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    def override_user():
        return sessions().query(models_User()).filter(
            models_User().id == uid).one()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    async def call(path):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            return await client.get(path)

    try:
        import asyncio as _asyncio

        previous_embed = embedder.embed_text
        embedder.embed_text = _no_embedding
        try:
            with _Captured("findback.observability") as events, \
                    _Captured("findback.api") as api_log:
                response = _asyncio.run(call(
                    f"/api/v1/search?q={PRIVATE_QUERY.replace(' ', '+')}"))
        finally:
            embedder.embed_text = previous_embed
        logged = events.text() + api_log.text()
    finally:
        app.dependency_overrides.clear()
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_sessionmaker

    assert response.status_code == 200, response.text
    for secret in (PRIVATE_QUERY, "rotating vault", "credentials"):
        assert secret not in logged, (
            f"{secret!r} reached a log:\n{logged[:800]}")


def models_User():
    import app.models as models
    return models.User


async def _no_embedding(text, task="document"):
    return None


def test_no_metric_label_can_carry_an_identifier():
    """An unbounded label would grow the time series forever and put a person's
    id in the scrape output."""
    rendered = metrics.render()
    for name in REQUIRED_METRICS:
        for key in _samples(rendered, name):
            for label in ("user_id=", "content_id=", "job_id=", "email="):
                assert label not in key, (
                    f"{name} carries {label} as a label: {key}")
    # The only labels in use are the outcome vocabulary.
    import re

    labels = set(re.findall(r'\{([^}]*)\}', rendered))
    assert labels <= {'outcome="ok"', 'outcome="key"', 'outcome="url"',
                      'outcome="item"', 'outcome="asset"', 'outcome="raised"',
                      'outcome="batch"', 'outcome="single"'}, labels


@needs_db
def test_the_metrics_endpoint_exposes_no_user_data(db, sessions):
    """`/metrics` is unauthenticated, so it must contain nothing an anonymous
    caller could learn about a person."""
    import httpx

    import app.models as models

    with sessions() as s:
        user = models.User(email="secret-person@example.test")
        s.add(user)
        s.flush()
        uid = user.id
        asset = models.ContentAsset(
            canonical_url="https://example.test/metrics-private",
            dedupe_key="url:https://example.test/metrics-private",
            visibility="PRIVATE", owner_user_id=uid,
            title=PRIVATE_PAGE)
        s.add(asset)
        s.flush()
        asset_id = asset.id
        s.commit()

    previous_engine = db_module._engine
    previous_sessionmaker = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = sessions
    try:
        async def call():
            transport = httpx.ASGITransport(app=app_of_main())
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as client:
                return await client.get("/metrics")

        response = asyncio.run(call())
    finally:
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_sessionmaker

    assert response.status_code == 200
    body = response.text
    for secret in ("secret-person@example.test", PRIVATE_PAGE, "Northwind",
                   str(uid), "example.test", str(asset_id)):
        assert secret not in body, (
            f"{secret!r} appears in the unauthenticated /metrics output")


def app_of_main():
    from app.main import app
    return app


# --- the worker process ---------------------------------------------------

class _WorkerLog:
    """Capture from the whole `findback` tree with the WORKER's filters on.

    A Celery worker never imports `app.main`, so the API's lifespan never runs
    there. `app/celery_app.py` installs the filters on `worker_process_init`
    instead, and this is that configuration: if the filters were not installed,
    the private text planted below would be sitting in `stream` at the end.
    """

    def __init__(self):
        self.stream = None
        self._handler = None
        self._logger = None
        self._previous = None

    def __enter__(self):
        import io
        import logging

        from app.log_filters import install_log_filters

        install_log_filters()
        self._logger = logging.getLogger("findback")
        self._previous = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self.stream = io.StringIO()
        self._handler = logging.StreamHandler(self.stream)
        self._handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc_info):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous)
        return False

    def text(self) -> str:
        self._handler.flush()
        return self.stream.getvalue()

    def events(self) -> list:
        out = []
        for line in self.text().splitlines():
            _, _, payload = line.partition(" ")
            payload = payload.strip()
            if payload.startswith("{"):
                try:
                    out.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
        return out


@needs_db
def test_the_worker_process_logs_no_private_content(sessions, db):
    """The phase's privacy rule, in the process that actually holds the text.

    Fetching, extraction and embedding all run in the worker. A failure in any
    of them quotes what it was working on, so the private page is planted, the
    pipeline is driven until it fails, and the assertion is that not one word of
    the page is in the worker's log output.
    """
    import asyncio as _asyncio

    from app import tasks as app_tasks
    from app.services import fetcher, pipeline, privacy

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(db_module.text(
            "INSERT INTO users (id, email) VALUES (:i, :e)"),
            {"i": uid, "e": "worker@example.test"})
        s.commit()
        asset_id = s.execute(db_module.text("""
            INSERT INTO content_assets (canonical_url, dedupe_key, visibility,
                                         owner_user_id)
            SELECT 'https://example.test/worker-private',
                   'url:https://example.test/worker-private', 'UNKNOWN', id
              FROM users WHERE email = 'worker@example.test'
            RETURNING id
        """)).scalar()
        s.execute(db_module.text(
            "INSERT INTO user_memories (user_id, content_id) VALUES (:u, :c)"),
            {"u": uid, "c": asset_id})
        item_id = s.execute(db_module.text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            VALUES (:u, 'https://example.test/worker-private',
                    'https://example.test/worker-private', :c, 'pending')
            RETURNING id
        """), {"u": uid, "c": asset_id}).scalar()
        s.commit()

    # The page, marked private exactly as the gateway marks it.
    privacy.register_private(PRIVATE_PAGE)

    saved_engine = db_module._engine
    saved_sessionmaker = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = sessions

    previous_fetch = fetcher.fetch_content

    async def _fetch(url, preview=""):
        # A fetcher that fails the way a real one does: the exception quotes
        # the page it was fetching.
        raise RuntimeError(f"could not fetch: {PRIVATE_PAGE}")

    fetcher.fetch_content = _fetch
    try:
        with _WorkerLog() as worker_log:
            app_tasks.process_item.apply(args=(str(item_id),), throw=False)
    finally:
        fetcher.fetch_content = previous_fetch
        db_module._engine = saved_engine
        db_module._sessionmaker = saved_sessionmaker

    logged = worker_log.text()
    assert logged, "the worker logged nothing at all, so this proves nothing"
    for secret in (PRIVATE_PAGE, "Northwind", "vault credentials",
                   "rotate the shared"):
        assert secret not in logged, (
            f"{secret!r} reached the worker log:\n{logged[:800]}")
    # The failure is still visible, which is what makes the redaction useful
    # rather than merely quiet.
    assert "could not fetch" in logged or "failed" in logged, logged[:400]


@needs_db
def test_the_worker_emits_the_structured_fields(sessions, db):
    """The phase's log fields have to be emitted from the worker too, or a
    pipeline failure would be a line of text with nothing to filter on."""
    import asyncio as _asyncio

    from app import tasks as app_tasks
    from app.services import embedder, extractor, fetcher, privacy, storage
    from app.schemas import Brief

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(db_module.text(
            "INSERT INTO users (id, email) VALUES (:i, :e)"),
            {"i": uid, "e": "worker-ok@example.test"})
        s.commit()
        asset_id = s.execute(db_module.text("""
            INSERT INTO content_assets (canonical_url, dedupe_key, visibility,
                                         owner_user_id)
            SELECT 'https://example.test/worker-ok',
                   'url:https://example.test/worker-ok', 'UNKNOWN', id
              FROM users WHERE email = 'worker-ok@example.test'
            RETURNING id
        """)).scalar()
        s.execute(db_module.text(
            "INSERT INTO user_memories (user_id, content_id) VALUES (:u, :c)"),
            {"u": uid, "c": asset_id})
        job_id = s.execute(db_module.text("""
            INSERT INTO processing_jobs (content_id, job_type)
            VALUES (:c, 'process_item:v1') RETURNING id
        """), {"c": asset_id}).scalar()
        item_id = s.execute(db_module.text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            VALUES (:u, 'https://example.test/worker-ok',
                    'https://example.test/worker-ok', :c, 'pending')
            RETURNING id
        """), {"u": uid, "c": asset_id}).scalar()
        s.commit()

    saved_engine = db_module._engine
    saved_sessionmaker = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = sessions

    saved = {
        "fetch": fetcher.fetch_content,
        "extract": extractor.extract_brief,
        "store": storage.store_raw_snapshot,
        "embed_many": embedder.embed_many,
        "embed_text": embedder.embed_text,
        "model": embedder.embedding_model_name,
    }

    async def _fetch(url, preview=""):
        return {"text": "a short page about deployment recovery",
                "title": "Deployment recovery", "source_type": "article"}

    async def _extract(text, url_title="", url=""):
        return Brief(title="Deployment recovery", overview="how to recover",
                     highlights=["roll back"], topics=["ops"], intent=["learn"],
                     structured_data={"content_type": "guide"})

    fetcher.fetch_content = _fetch
    extractor.extract_brief = _extract
    storage.store_raw_snapshot = lambda i, p: None

    async def _embed_many(texts, task="document"):
        return [[0.0] * 1536 for _ in texts]

    embedder.embed_many = _embed_many
    embedder.embed_text = lambda text, task="document": [0.0] * 1536
    embedder.embedding_model_name = lambda: "test-model"

    try:
        with _WorkerLog() as worker_log:
            app_tasks.process_item.apply(args=(str(item_id),), throw=False)
    finally:
        fetcher.fetch_content = saved["fetch"]
        extractor.extract_brief = saved["extract"]
        storage.store_raw_snapshot = saved["store"]
        embedder.embed_many = saved["embed_many"]
        embedder.embed_text = saved["embed_text"]
        embedder.embedding_model_name = saved["model"]
        db_module._engine = saved_engine
        db_module._sessionmaker = saved_sessionmaker

    events = worker_log.events()
    stages = [e for e in events if e.get("event") == "pipeline.stage_completed"]
    assert stages, f"the worker emitted no stage events: {worker_log.text()[:400]}"
    # Every stage of the pipeline is reported, each with the phase's fields.
    from app.services import pipeline as pipeline_module

    reported = {e["stage"] for e in stages}
    assert reported == set(pipeline_module.STAGE_ORDER), reported
    for event in stages:
        for field in ("stage", "status", "duration_ms", "job_id",
                      "content_id", "pipeline_version"):
            assert field in event, f"{field} missing from {event}"
        assert event["pipeline_version"] == "process_item:v1"
        assert event["job_id"] == observability.digest_id(str(job_id)), (
            "job_id must be the pseudonymous form, never the raw id")

    completed = [e for e in events if e.get("event") == "job.completed"]
    assert completed, worker_log.text()[:400]
    assert completed[0]["status"] == "ready"
    assert completed[0]["duration_ms"] >= 0
