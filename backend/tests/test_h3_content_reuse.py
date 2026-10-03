"""H3: is one PUBLIC piece of content processed once, or once per user?

MEASUREMENT, NOT A REQUIREMENT.

This file pins the behaviour H3 describes so the claim can be checked rather
than argued about: two users save the same PUBLIC URL, the expensive stages are
counted, and the counts are printed. It asserts the *current* behaviour, so when
content-level reuse is implemented these assertions must flip -- the docstring on
each one says what it should become.

Why it matters: fetch, the model call and the embeddings are the only parts of a
save that cost money, and this measures how often they run for content that is
already processed.

    TEST_DATABASE_URL=... python -m pytest tests/test_h3_content_reuse.py -q -s
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping the H3 measurement",
)

SHARED_URL = "https://public.example.test/shared-article"


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


@pytest.fixture
def spies(monkeypatch):
    """Count the three stages that cost money, and fake the outside world."""
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    calls = {"fetch": 0, "understand": 0, "embed": 0, "embedded_texts": 0}

    async def fetch(url, preview=""):
        calls["fetch"] += 1
        return {"text": "a shared body about kubernetes rollbacks and deploys. "
                        "Long enough to survive the length checks.",
                "title": "Shared thing", "source_type": "article"}

    async def extract(raw_text, url_title="", url=""):
        calls["understand"] += 1
        return Brief(title="Shared thing",
                     overview="about kubernetes rollbacks and deploys",
                     highlights=["automated rollback"], topics=["deploy"],
                     intent=["learn"],
                     structured_data={"content_type": "general"})

    async def embed_many(texts, task="document"):
        calls["embed"] += 1
        calls["embedded_texts"] += len(texts)
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(fetcher, "fetch_content", fetch)
    monkeypatch.setattr(extractor, "extract_brief", extract)
    monkeypatch.setattr(embedder, "embed_many", embed_many)
    monkeypatch.setattr(embedder, "embed_text",
                        lambda text, task="document": [0.0] * 1536)
    monkeypatch.setattr(embedder, "embedding_model_name", lambda: "spy-model")
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
        app_tasks.process_item.apply(args=(str(item_id),), throw=True)
    finally:
        db_module._engine = previous_engine
        db_module._sessionmaker = previous_sessionmaker


def test_h3_public_content_is_reprocessed_for_every_user(
        db, sessions, users, spies, monkeypatch):
    from app.tasks import process_item

    monkeypatch.setattr(process_item, "delay", lambda item_id: None)
    author, second = users

    # 1. The first saver's save runs the whole pipeline.
    with sessions() as s:
        first_item = _save(s, author)
    _run_worker(sessions, first_item)
    first_pass = dict(spies)
    assert first_pass["fetch"] == 1
    assert first_pass["understand"] == 1
    assert first_pass["embed"] == 1, "one call for the memory, one for the chunk"

    with sessions() as s:
        content_id = s.execute(
            text("SELECT content_id FROM items WHERE id = :i"),
            {"i": str(first_item)}).scalar()
        chunks_after_first = s.execute(text(
            "SELECT count(*) FROM chunks")).scalar()

    # 2. The content is classified PUBLIC, which is what makes it reusable.
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET visibility = 'PUBLIC', "
                       "owner_user_id = NULL"))
        s.commit()
        asset = s.execute(text(
            "SELECT processing_status, title, brief, structured_data::text "
            "FROM content_assets WHERE id = :c"),
            {"c": content_id}).mappings().one()

    assert asset["processing_status"] == "READY", (
        "the asset is finished at the current pipeline version before the "
        "second save, which is what a reuse check would look at")
    assert asset["title"] and asset["brief"]

    # 3. A second user saves the same link. Nothing about the content changed.
    for key in spies:
        spies[key] = 0

    with sessions() as s:
        second_item = _save(s, second)
    assert str(second_item) != str(first_item), "two users, two items"
    with sessions() as s:
        same_asset = s.execute(
            text("SELECT content_id FROM items WHERE id = :i"),
            {"i": str(second_item)}).scalar()
    assert str(same_asset) == str(content_id), "and one shared asset"

    _run_worker(sessions, second_item)

    with sessions() as s:
        chunks_total = s.execute(text("SELECT count(*) FROM chunks")).scalar()
        chunks_by_item = s.execute(text(
            "SELECT count(DISTINCT item_id) FROM chunks")).scalar()
        jobs = s.execute(text(
            "SELECT status, count(*) FROM processing_jobs GROUP BY status"
        )).all()

    print()
    print("  === H3 measurement: two users, one PUBLIC asset ===")
    print(f"  first save   : fetch={first_pass['fetch']} "
          f"understand={first_pass['understand']} embed={first_pass['embed']}")
    print(f"  second save  : fetch={spies['fetch']} "
          f"understand={spies['understand']} embed={spies['embed']} "
          f"(texts embedded: {spies['embedded_texts']})")
    print(f"  chunks       : {chunks_after_first} after the first save, "
          f"{chunks_total} after the second, "
          f"across {chunks_by_item} distinct item(s)")
    print(f"  jobs         : {jobs}")
    print()

    # The assertions below pin CURRENT behaviour (H3 present). After a
    # content-level reuse fix they must become 0/0/0 and chunks_by_item == 1.
    assert spies["fetch"] == 1, "H3: the page is fetched again for the second user"
    assert spies["understand"] == 1, "H3: the model is called again"
    assert spies["embed"] == 1, "H3: the content is embedded again"
    assert chunks_by_item == 2, (
        "H3: chunk rows are keyed per item, so one shared piece of content now "
        "has a set of chunks for each user who saved it")


def test_h3_the_asset_already_holds_everything_a_reuse_would_need(
        db, sessions, users, spies, monkeypatch):
    """What is already available to reuse, measured rather than assumed.

    If the asset's own columns and its chunks are complete, the second user's
    item needs no pipeline at all -- only the item row that points at it. This
    test reports exactly which pieces those are, so the proposal is grounded in
    what the current schema already stores.
    """
    from app.tasks import process_item

    monkeypatch.setattr(process_item, "delay", lambda item_id: None)
    author, second = users

    with sessions() as s:
        first_item = _save(s, author)
    _run_worker(sessions, first_item)
    with sessions() as s:
        s.execute(text("UPDATE content_assets SET visibility = 'PUBLIC', "
                       "owner_user_id = NULL"))
        s.commit()
        content_id = s.execute(
            text("SELECT content_id FROM items WHERE id = :i"),
            {"i": str(first_item)}).scalar()

    with sessions() as s:
        asset = s.execute(text(
            "SELECT processing_status, pipeline_version, title, brief, "
            "structured_data::text AS structured, entities::text AS entities, "
            "topics::text AS topics, intent, processed_at "
            "FROM content_assets WHERE id = :c"),
            {"c": content_id}).mappings().one()
        chunks = s.execute(text(
            "SELECT count(*), count(embedding) FROM chunks "
            "WHERE item_id = :i"), {"i": str(first_item)}).one()
        item_columns = s.execute(text(
            "SELECT search_text IS NOT NULL, embedding IS NOT NULL "
            "FROM items WHERE id = :i"), {"i": str(first_item)}).one()

    print()
    print("  === what the asset already holds ===")
    print(f"  processing_status : {asset['processing_status']}")
    print(f"  pipeline_version  : {asset['pipeline_version']!r}")
    print(f"  title/brief       : {bool(asset['title'])}/{bool(asset['brief'])}")
    print(f"  structured_data   : {bool(asset['structured'])}")
    print(f"  entities/topics   : {bool(asset['entities'])}/{bool(asset['topics'])}")
    print(f"  processed_at      : {asset['processed_at'] is not None}")
    print(f"  chunks on the item: {chunks[0]} ({chunks[1]} with a vector)")
    print(f"  item search_text/embedding present: {item_columns}")
    print()

    assert asset["processing_status"] == "READY"
    assert asset["title"] and asset["brief"] and asset["processed_at"]
    assert chunks[0] >= 1 and chunks[1] >= 1, "the asset has vectors already"
    # The gap: those chunks hang off the FIRST user's item, and the asset names
    # no pipeline version, so nothing records which pipeline produced it.
    assert asset["pipeline_version"] is None, (
        "the asset carries no pipeline_version even though the job row does")
