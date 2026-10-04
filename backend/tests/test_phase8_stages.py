"""Phase 8: explicit pipeline stages that a retry resumes from.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase8_stages.py -q

Every stage is exercised as a failure point. The central claim is that a retry
starts at the stage after the last one that succeeded, so an EMBED failure
never repeats FETCH and never repeats the AI call.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 8 stage tests",
)

ALL_STAGES = ["FETCH", "NORMALIZE", "UNDERSTAND", "BRIEF", "CHUNK", "EMBED"]


class Env:
    """Records which stages ran, and injects a failure at a chosen stage."""

    def __init__(self):
        self.calls = []
        self.fail = None


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
    name = f"fb_phase8_{uuid.uuid4().hex[:10]}"
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
def env(monkeypatch):
    """Wrap every stage in a recorder so failures can be injected per stage.

    The wrappers delegate to the real implementation unless `env.fail` names
    their stage, so a test can break exactly one stage and observe that the
    retry skips the ones before it.
    """
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, pipeline, storage

    state = Env()

    def record(stage):
        state.calls.append(stage)
        if state.fail == stage:
            raise RuntimeError(f"{stage} failed")

    async def _fetch(url, preview=""):
        record("FETCH")
        return {"text": "some  content here", "title": "T", "source_type": "web"}

    async def _extract(text, title="", url=""):
        # UNDERSTAND is recorded by the stage wrapper below, not here: this
        # replaces the model's extraction call, and the stage is the unit tested.
        return Brief(title="Clean", overview="S", highlights=["k"],
                     topics=["dinner"], intent=["cook"],
                     structured_data={"content_type": "recipe"})

    async def _embed(texts, task="document"):
        record("EMBED")
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(fetcher, "fetch_content", _fetch)
    monkeypatch.setattr(extractor, "extract_brief", _extract)
    monkeypatch.setattr(embedder, "embed_many", _embed)
    monkeypatch.setattr(storage, "store_raw_snapshot",
                        lambda item_id, payload: None)
    monkeypatch.setattr(embedder, "chunk_text_with_timestamps",
                        lambda text, size=800, overlap=160: [
                            {"chunk_idx": 0, "chunk_text": text,
                             "start_timestamp": None, "start_seconds": None}])
    monkeypatch.setattr(embedder, "embedding_model_name", lambda: "test-model")

    real_normalize = pipeline.stage_normalize
    real_chunk = pipeline.stage_chunk
    real_understand = pipeline.stage_understand
    real_brief = pipeline.stage_brief

    async def stage_normalize(item):
        record("NORMALIZE")
        await real_normalize(item)

    async def stage_chunk(item):
        record("CHUNK")
        await real_chunk(item)

    # UNDERSTAND produces the brief's inputs and BRIEF stores them, so
    # UNDERSTAND records first: the recorded order must equal ALL_STAGES.
    async def stage_understand(item):
        record("UNDERSTAND")
        await real_understand(item)

    async def stage_brief(item):
        record("BRIEF")
        await real_brief(item)

    monkeypatch.setattr(pipeline, "stage_understand", stage_understand)
    monkeypatch.setattr(pipeline, "stage_brief", stage_brief)
    monkeypatch.setattr(pipeline, "stage_normalize", stage_normalize)
    monkeypatch.setattr(pipeline, "stage_chunk", stage_chunk)
    # `_STAGES` is resolved by the runner at call time, so the recorded versions
    # are what actually executes.
    monkeypatch.setitem(pipeline._STAGES, "UNDERSTAND", stage_understand)
    monkeypatch.setitem(pipeline._STAGES, "BRIEF", stage_brief)
    monkeypatch.setitem(pipeline._STAGES, "NORMALIZE", stage_normalize)
    monkeypatch.setitem(pipeline._STAGES, "CHUNK", stage_chunk)
    return state


def _seed(sessions, index=0):
    """One user, one asset, one item, one queued job."""
    import app.models as models
    from app.services.outbox import record_job

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": f"s{index}@example.test"})
        s.commit()
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url=f"https://example.test/staged-{index}",
            dedupe_key=f"url:https://example.test/staged-{index}",
            owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        # Phase 16: items_user_content_fk requires this row to exist before an
        # item may name the asset. Real saves always create it, so the fixture
        # creates it too. Setup only.
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        item = models.Item(user_id=uid, url=asset.canonical_url,
                           canonical_url=asset.canonical_url,
                           title=f"Page {index}", content_id=asset.id)
        s.add(item)
        s.flush()
        job = record_job(s, asset.id, models.JOB_TYPE_PROCESS)
        s.commit()
        item_id, asset_id, job_id = str(item.id), str(asset.id), str(job.id)
    # Eligible for pickup now, so the worker sees it without a wait.
    with sessions() as s:
        s.execute(text(
            "UPDATE processing_jobs SET available_at=now() WHERE id = :j"),
            {"j": job_id})
        s.commit()
    return item_id, asset_id, job_id


def _run(db, item_id):
    """Run the real worker for one item, against the test database."""
    import app.tasks as app_tasks
    from app import database as db_module

    previous = db_module._engine
    previous_factory = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = None
    # One `_run` must equal one attempt. Celery's own retry would re-enter the
    # task and re-run stages, which is exactly what these tests must observe
    # happening (or not) on an explicit retry instead.
    previous_retries = app_tasks.process_item.max_retries
    app_tasks.process_item.max_retries = 0
    try:
        app_tasks.process_item.apply(args=(item_id,), throw=False)
    finally:
        app_tasks.process_item.max_retries = previous_retries
        db_module._engine = previous
        db_module._sessionmaker = previous_factory


def _job(db, job_id):
    with db.connect() as conn:
        return conn.execute(text(
            "SELECT status, attempt_count, last_stage FROM processing_jobs "
            "WHERE id = :j"), {"j": job_id}).mappings().one()


def _release(db):
    with db.connect() as conn:
        conn.execute(text("UPDATE processing_jobs SET available_at=now()"))


def test_stages_run_in_the_declared_order(db, sessions, env):
    """A clean run visits every stage once, in the declared order."""
    item_id, _asset, job_id = _seed(sessions)
    _run(db, item_id)

    assert env.calls == ALL_STAGES
    job = _job(db, job_id)
    assert job["status"] == "READY"
    assert job["last_stage"] == "EMBED", "the last completed stage is recorded"


@pytest.mark.parametrize("failed", ALL_STAGES)
def test_a_failure_at_each_stage_is_recorded(db, sessions, env, failed):
    """Every stage can fail, and the job stays retryable instead of vanishing."""
    index = ALL_STAGES.index(failed)
    item_id, _asset, job_id = _seed(sessions, index)
    env.fail = failed

    _run(db, item_id)

    job = _job(db, job_id)
    assert job["status"] == "PENDING", "a failed stage must stay retryable"
    assert job["attempt_count"] == 1
    # last_stage names the last stage that SUCCEEDED, so it is the one before.
    assert job["last_stage"] == (ALL_STAGES[index - 1] if index else None)
    # Nothing past the failing stage may have run.
    assert env.calls == ALL_STAGES[:index + 1]


@pytest.mark.parametrize("failed", ALL_STAGES)
def test_retry_resumes_at_the_failed_stage(db, sessions, env, failed):
    """The retry starts at the failing stage; earlier stages do not re-run."""
    index = ALL_STAGES.index(failed)
    item_id, _asset, job_id = _seed(sessions, 100 + index)

    env.fail = failed
    _run(db, item_id)
    assert _job(db, job_id)["status"] == "PENDING"

    env.fail = None
    env.calls.clear()
    _release(db)
    _run(db, item_id)

    for stage in ALL_STAGES[:index]:
        assert stage not in env.calls, \
            f"{stage} ran again; the retry must resume at {failed}"
    assert env.calls == ALL_STAGES[index:]
    job = _job(db, job_id)
    assert job["status"] == "READY"
    assert job["last_stage"] == "EMBED"


def test_an_embed_failure_does_not_repeat_fetch_or_the_ai_call(db, sessions,
                                                               env):
    """The headline case: EMBED fails, so FETCH and the AI call are not paid twice."""
    item_id, _asset, job_id = _seed(sessions, 900)

    env.fail = "EMBED"
    _run(db, item_id)
    assert _job(db, job_id)["last_stage"] == "CHUNK"

    env.fail = None
    env.calls.clear()
    _release(db)
    _run(db, item_id)

    assert "FETCH" not in env.calls, "the retry must not refetch"
    assert "UNDERSTAND" not in env.calls, "the retry must not call the AI again"
    assert env.calls == ["EMBED"]
    assert _job(db, job_id)["status"] == "READY"


def test_retry_does_not_duplicate_chunks(db, sessions, env):
    """Re-running EMBED replaces chunk rows instead of appending to them."""
    item_id, _asset, _job_id = _seed(sessions, 901)

    env.fail = "EMBED"
    _run(db, item_id)
    env.fail = None
    _release(db)
    _run(db, item_id)

    with db.connect() as conn:
        count = conn.execute(text(
            "SELECT count(*) FROM chunks WHERE item_id = :i"),
            {"i": item_id}).scalar()
    assert count == 1, "an idempotent EMBED must not duplicate chunk rows"


def test_resume_index_maps_a_completed_stage_to_the_next_one():
    from app.services.pipeline import resume_index

    assert resume_index(None) == 0
    assert resume_index("") == 0
    assert resume_index("NOT_A_STAGE") == 0
    assert resume_index("FETCH") == 1
    assert resume_index("NORMALIZE") == 2
    assert resume_index("UNDERSTAND") == 3
    assert resume_index("BRIEF") == 4
    assert resume_index("CHUNK") == 5
    assert resume_index("EMBED") == 6, "nothing left to resume"


def test_the_brief_stores_its_highlights_verbatim(db, sessions, env):
    """The brief's highlights reach the stored columns without being truncated.

    Phase 9 replaced the old summary+3 shape, so this no longer pins that shape;
    what still holds is that highlights survive intact, and that the full brief
    is kept in structured_data for free-form storage.
    """
    item_id, _asset, _job_id = _seed(sessions, 902)
    _run(db, item_id)

    with db.connect() as conn:
        row = conn.execute(text(
            "SELECT summary, key_points, category, fetch_metadata FROM items "
            "WHERE id = :i"), {"i": item_id}).mappings().one()
    assert row["summary"] == "S"
    assert row["key_points"] == ["k"]
    assert row["category"] == "recipe"
    assert row["fetch_metadata"]["brief"]["overview"] == "S"
    _release(db)
