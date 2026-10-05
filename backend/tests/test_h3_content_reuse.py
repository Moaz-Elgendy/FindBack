"""H3 step 1: processed PUBLIC content is reused, not reprocessed.

The measurement that opened this file (two users, one PUBLIC asset, the
expensive stages counted) said the second save re-fetched the page, re-called
the model and re-embedded the content. These tests now pin the behaviour that
replaced it, and the conditions under which reuse must NOT happen.

    TEST_DATABASE_URL=... python -m pytest tests/test_h3_content_reuse.py -q
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping the H3 reuse tests",
)

SHARED_URL = "https://public.example.test/shared-article"
MODEL = "spy-model"
OTHER_MODEL = "some-other-model"


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
    name = f"fb_h3_{uuid.uuid4().hex[:10]}"
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
def users(sessions):
    ids = [uuid.uuid4(), uuid.uuid4()]
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  [{"i": str(uid), "e": f"h3-{n}@example.test"}
                   for n, uid in enumerate(ids)])
        s.commit()
    return ids


def _add_user(sessions, label):
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": str(uid), "e": f"h3-{label}@example.test"})
        s.commit()
    return uid


@pytest.fixture
def spies(monkeypatch):
    """Count the three stages that cost money, and fake the outside world."""
    from app.schemas import Brief
    from app.services import brief_v2, embedder, extractor, fetcher, storage

    calls = {"fetch": 0, "understand": 0, "embed": 0}
    vector = [0.001] * 1536

    async def fetch(url, preview=""):
        calls["fetch"] += 1
        return {"text": "a shared body about kubernetes rollbacks and deploys. "
                        "Long enough to survive the length checks.",
                "title": "Shared thing", "source_type": "article", "input_provenance": "page"}

    async def extract(raw_text, url_title="", url=""):
        calls["understand"] += 1
        return Brief(title="Shared thing",
                     overview="about kubernetes rollbacks and deploys",
                     highlights=["automated rollback"], topics=["deploy"],
                     intent=["learn"],
                     structured_data={"content_type": "general"})

    async def extract_v2(evidence):
        from app.schemas import BriefV2
        old = await extract(evidence.get("caption", ""), evidence.get("title", ""))
        from test_brief_v2 import payload
        data = payload()
        data.update(title=old.title, instant_brief=old.overview,
                    key_points=[{"point": p, "source_ref": "caption"} for p in old.highlights],
                    entities={"tools_products": ["kubernetes"], "people_orgs": [], "numbers": []},
                    search_phrases=["Find Kubernetes rollbacks.", "Automated rollback.", "Kubernetes deploys.",
                                    "Shared deployment article.", "Rollback deployment tools."])
        return BriefV2(**data)

    async def embed_many(texts, task="document"):
        calls["embed"] += 1
        return [list(vector) for _ in texts]

    async def embed_text(text, task="document"):
        # hybrid_search awaits this, so it has to be a coroutine.
        return list(vector)

    monkeypatch.setattr(fetcher, "fetch_content", fetch)
    monkeypatch.setattr(extractor, "extract_brief", extract)
    monkeypatch.setattr(brief_v2, "extract", extract_v2)
    monkeypatch.setattr(embedder, "embed_many", embed_many)
    monkeypatch.setattr(embedder, "embed_text", embed_text)
    monkeypatch.setattr(embedder, "embedding_model_name", lambda: MODEL)
    monkeypatch.setattr(embedder, "chunk_text_with_timestamps",
                        lambda text, size=800, overlap=160: [
                            {"chunk_idx": 0, "chunk_text": text,
                             "start_timestamp": None, "start_seconds": None}])
    monkeypatch.setattr(storage, "store_raw_snapshot",
                        lambda item_id, payload: None)
    return calls


class _UserRow:
    def __init__(self, uid):
        self.id = uid


def _save(session, user_id, url=SHARED_URL):
    """A real save through the API handler, with the queue publish stubbed."""
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint="Shared thing",
                                  preview="a preview"),
                    session, _UserRow(user_id))
    session.commit()
    return result.id


def _run_worker(sessions, item_id):
    """Run the real Celery task body against the throwaway database."""
    from app import database as db_module
    from app import tasks as app_tasks

    previous_engine = db_module._engine
    previous_sessionmaker = db_module._sessionmaker
    db_module._engine = sessions.kw["bind"]
    db_module._sessionmaker = None
    try:
        return app_tasks.process_item.apply(args=(str(item_id),),
                                            throw=True).get()
    finally:
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_sessionmaker


def _search(db, uid, query, limit=10):
    from app.services import search

    with Session(bind=db) as s:
        return asyncio.run(search.hybrid_search(s, uid, query, limit=limit))


def _publish_public(sessions, content_id):
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET visibility = 'PUBLIC', "
                       "owner_user_id = NULL WHERE id = :c"),
                  {"c": content_id})
        s.commit()


def _content_id_of(sessions, item_id):
    with sessions() as s:
        return s.execute(text("SELECT content_id FROM items WHERE id = :i"),
                         {"i": str(item_id)}).scalar()


def _asset(sessions, content_id):
    with sessions() as s:
        return s.execute(text(
            "SELECT processing_status, pipeline_version, title, brief, "
            "structured_data::text AS structured, entities::text AS entities, "
            "topics::text AS topics, processed_at "
            "FROM content_assets WHERE id = :c"),
            {"c": content_id}).mappings().one()


def _item_state(sessions, item_id):
    with sessions() as s:
        return s.execute(text(
            "SELECT status, embedding IS NOT NULL AS has_vector, "
            "embedding_model, coalesce(search_text, '') AS search_text, "
            "(SELECT count(*) FROM chunks WHERE item_id = :i) AS chunks "
            "FROM items WHERE id = :i"),
            {"i": str(item_id)}).mappings().one()


def _prepared_public_content(sessions, users, monkeypatch):
    """User A saves, the worker processes it, and the content becomes PUBLIC."""
    from app.tasks import process_item

    monkeypatch.setattr(process_item, "delay", lambda item_id: None)
    author, second = users
    with sessions() as s:
        first_item = _save(s, author)
    _run_worker(sessions, first_item)
    content_id = _content_id_of(sessions, first_item)
    _publish_public(sessions, content_id)
    return author, second, first_item, content_id


# --- the fix itself ---------------------------------------------------------

def test_a_second_user_reuses_processed_public_content(
        db, sessions, users, spies, monkeypatch):
    """The second save costs nothing: no fetch, no model call, no embedding.

    This is the measurement that opened this file, inverted. The counts that
    used to read fetch=1 understand=1 embed=1 for the second user now read 0,
    and the second user still ends up with a finished, searchable memory.
    """
    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)

    asset_before = _asset(sessions, content_id)
    assert asset_before["processing_status"] == "READY"
    assert asset_before["pipeline_version"] == "process_item:v1", \
        "a finished run must stamp the asset, or nothing can be reused"

    for key in spies:
        spies[key] = 0

    with sessions() as s:
        second_item = _save(s, second)
    assert str(second_item) != str(first_item), "two users, two items"
    result = _run_worker(sessions, second_item)

    print()
    print("  === reuse: the second user's save ===")
    print(f"  stage calls : fetch={spies['fetch']} "
          f"understand={spies['understand']} embed={spies['embed']}")
    print(f"  worker      : {result}")
    print()

    assert spies["fetch"] == 0, "the page must not be fetched again"
    assert spies["understand"] == 0, "the model must not be called again"
    assert spies["embed"] == 0, "the content must not be embedded again"
    assert result["reused"] is True

    state = _item_state(sessions, second_item)
    assert state["status"] == "ready", "search only sees ready items"
    assert state["has_vector"] is True, "the memory vector was handed over"
    assert state["embedding_model"] == MODEL
    assert state["chunks"] >= 1, "search reaches content through chunks"
    assert state["search_text"], "the lexical document was recomputed"

    # ... and the second user can actually find it, through their own row only.
    results, _took = _search(db, second, "kubernetes rollbacks")
    ranked = [str(r["row"][0]) for r in results]
    assert ranked, "the reused memory is not searchable"
    assert str(second_item) in ranked
    assert str(first_item) not in ranked, "the first user's item is not theirs"

    # The asset is untouched by the reuse: it was already finished.
    assert _asset(sessions, content_id)["processing_status"] == "READY"


def test_a_reused_job_still_goes_through_the_state_machine(
        db, sessions, users, spies, monkeypatch):
    """Reuse is not a shortcut around Phase 5/6 -- only around the stages.

    The job is recorded, claimed, and completed READY like any other, so the
    outbox, the partial unique index and the state machine see nothing new. The
    one difference is deliberate: a reuse ran no pipeline, so it carries no
    claim time and does not land in the Phase 18 duration histograms as a
    multi-second processing run.
    """
    from app.services import metrics

    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)

    for key in spies:
        spies[key] = 0
    with sessions() as s:
        second_item = _save(s, second)
    _run_worker(sessions, second_item)

    with sessions() as s:
        jobs = s.execute(text(
            "SELECT status, attempt_count, claimed_at, last_stage "
            "FROM processing_jobs WHERE content_id = :c ORDER BY created_at"),
            {"c": content_id}).mappings().all()
    assert len(jobs) == 2, "one job per save, no extra jobs invented"
    assert all(j["status"] == "READY" for j in jobs)
    assert all(j["attempt_count"] == 1 for j in jobs)

    first, reused = jobs
    assert first["last_stage"] == "EMBED", "a real run records its stages"
    assert reused["last_stage"] is None, "a reuse completed no stage"
    assert first["claimed_at"] is not None, "a real run keeps its claim time"
    assert reused["claimed_at"] is None, (
        "a reuse has no processing duration to report, so it is excluded from "
        "the Phase 18 histograms rather than logged as a very fast pipeline")

    # Proven through the metrics the scraper reads, not just through the row.
    with sessions() as s:
        metrics.reset()
        summary = metrics.collect_queue_metrics(s)
    assert summary["ok"] is True
    assert summary["counts"]["total"] == 2
    assert summary["counts"]["ready"] == 2, "both jobs are successes"
    assert metrics.processing_duration.count() == 1, (
        "only the run that actually processed should be a duration sample")
    assert metrics.queue_wait_time.count() == 1


def test_a_model_mismatch_falls_back_to_the_full_pipeline(
        db, sessions, users, spies, monkeypatch):
    """Vectors from another model are not comparable with this one's query.

    Reusing them would give the second user a memory that scores nonsense
    against every search, so a mismatch means paying for the pipeline.
    """
    from app.services import embedder

    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)

    monkeypatch.setattr(embedder, "embedding_model_name", lambda: OTHER_MODEL)
    for key in spies:
        spies[key] = 0

    with sessions() as s:
        second_item = _save(s, second)
    result = _run_worker(sessions, second_item)

    assert spies["fetch"] == 1, "the page is fetched again for a new model"
    assert spies["understand"] == 1
    assert spies["embed"] == 1
    assert result.get("reused") is not True
    assert _item_state(sessions, second_item)["embedding_model"] == OTHER_MODEL


def test_a_private_or_unknown_asset_is_never_reused_across_users(
        sessions, users, spies, monkeypatch):
    """The worker re-checks Phase 4's rule; it does not trust the save.

    `_find_reusable_asset` decides at save time whether a second user may attach
    to an asset, and this asset was PUBLIC then. Visibility is not frozen
    afterwards, so the worker has to apply the rule again rather than copy
    whatever the save was once allowed to do.
    """
    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)

    # The second user saves while the content is PUBLIC, so the save is allowed
    # and records its job...
    with sessions() as s:
        second_item = _save(s, second)

    # ...and the content is private again before the worker gets to it. This is
    # a real window: the save records the intent, the worker decides later.
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET visibility = 'UNKNOWN', "
                       "owner_user_id = :a WHERE id = :c"),
                  {"a": str(author), "c": content_id})
        s.commit()

    for key in spies:
        spies[key] = 0
    result = _run_worker(sessions, second_item)

    assert spies["fetch"] == 1, "private content must be processed, not copied"
    assert spies["understand"] == 1
    assert spies["embed"] == 1
    assert result.get("reused") is not True
    assert _item_state(sessions, second_item)["chunks"] >= 1


def test_an_unstamped_asset_runs_once_more_and_is_then_reusable(
        db, sessions, users, spies, monkeypatch):
    """NULL means "processed before the stamp existed": run once, then stamp.

    Nothing is backfilled, so this is the path every existing asset takes the
    first time it is saved again -- and it must end reusable, not stuck.
    """
    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET pipeline_version = NULL "
                       "WHERE id = :c"), {"c": content_id})
        s.commit()

    for key in spies:
        spies[key] = 0
    with sessions() as s:
        second_item = _save(s, second)
    result = _run_worker(sessions, second_item)

    assert spies["fetch"] == 1, "an unstamped asset is processed, not reused"
    assert result.get("reused") is not True
    assert _asset(sessions, content_id)["pipeline_version"] == "process_item:v1"

    # Re-stamped, so the next user's save is free again.
    third = _add_user(sessions, "third")
    for key in spies:
        spies[key] = 0
    with sessions() as s:
        third_item = _save(s, third)
    result = _run_worker(sessions, third_item)

    assert spies["fetch"] == 0 and spies["understand"] == 0
    assert spies["embed"] == 0
    assert result["reused"] is True


def test_a_users_note_never_travels_with_the_content(
        db, sessions, users, spies, monkeypatch):
    """The reuse moves shared content only.

    The author's note is written before the second save, so if the copy reached
    into `user_memories` the second user would be holding it. Afterwards the
    second user writes a note of their own, and the author must not be able to
    find it -- neither through search nor in any stored column.
    """
    from app.services import user_context

    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)

    with sessions() as s:
        user_context.set_context(s, author, content_id,
                                 note="alice private thought zanzibar")

    for key in spies:
        spies[key] = 0
    with sessions() as s:
        second_item = _save(s, second)
    _run_worker(sessions, second_item)

    with sessions() as s:
        notes = dict(s.execute(text(
            "SELECT user_id::text, coalesce(user_note, '') FROM user_memories "
            "WHERE content_id = :c"), {"c": content_id}).all())
    assert "zanzibar" in notes[str(author)]
    assert notes[str(second)] == "", (
        "the author's note was copied onto the second user's memory")

    # The second user writes their own note.
    with sessions() as s:
        user_context.set_context(s, second, content_id,
                                 note="bob secret recipe karakul")

    # Nothing of it is visible to the author. Asserted on the note candidate
    # list itself, because the spy's constant vectors make every item a vector
    # hit for every query -- "no results at all" is not what is being tested.
    from app.services import search

    with Session(bind=db) as s:
        assert search.note_search(s, author, ["karakul"]) == [], \
            "another user's note must never be a candidate for the author"
        assert len(search.note_search(s, second, ["karakul"])) == 1, \
            "the note's own author must still find it"

    author_hits, _took = _search(db, author, "karakul")
    assert all("karakul" not in (r["match_reason"] or "")
               for r in author_hits), "the note was used as the author's reason"
    assert all(str(r["row"][0]) != str(second_item) for r in author_hits), \
        "the noted item belongs to the other user"

    with sessions() as s:
        asset_blob = " ".join(str(v) for row in s.execute(text(
            "SELECT brief, title, structured_data::text, entities::text, "
            "topics::text FROM content_assets")).all() for v in row)
        item_blob = " ".join(str(v) for row in s.execute(text(
            "SELECT coalesce(title, '') || ' ' || coalesce(title_clean, '') "
            "|| ' ' || coalesce(summary, '') || ' ' || coalesce(search_text, '') "
            "FROM items")).all() for v in row)
    assert "karakul" not in asset_blob, "the note reached the shared asset"
    assert "karakul" not in item_blob, "the note reached an item"

    # The second user still finds their own note.
    second_hits, _took = _search(db, second, "karakul")
    assert [str(r["row"][0]) for r in second_hits] == [str(second_item)]


def test_the_asset_holds_everything_a_reuse_needs(
        db, sessions, users, spies, monkeypatch):
    """What the asset carries once a real run has finished.

    The same inventory the pre-fix measurement printed, now with the pipeline
    version present: it is what makes the reuse gate decidable.
    """
    author, second, first_item, content_id = _prepared_public_content(
        sessions, users, monkeypatch)
    asset = _asset(sessions, content_id)

    assert asset["processing_status"] == "READY"
    assert asset["pipeline_version"] == "process_item:v1"
    assert asset["title"] and asset["brief"] and asset["processed_at"]
    assert asset["structured"] and asset["entities"] and asset["topics"]

    source = _item_state(sessions, first_item)
    assert source["has_vector"] is True
    assert source["chunks"] >= 1
    assert source["search_text"]
