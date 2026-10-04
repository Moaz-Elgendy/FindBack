"""One search embeds the query exactly once.

`hybrid_search` needs the query vector twice: once for the item-vector candidate
list and once for the chunk-vector list. Both used to call the provider
separately, so every search paid for the same string twice. `chunk_search` now
takes the vector the caller already has.

This counts the provider calls rather than trusting the reading of the code.

    TEST_DATABASE_URL=... python -m pytest tests/test_search_query_embedding.py -q
"""
import asyncio
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping the query-embedding test",
)


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
    name = f"fb_qemb_{uuid.uuid4().hex[:10]}"
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


def test_one_search_embeds_the_query_once(db, sessions, monkeypatch):
    """The vector is computed once and reused by both candidate lists."""
    import app.models as models
    from app.services import embedder, search

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": str(uid), "e": f"qemb-{uid.hex[:8]}@example.test"})
        asset = models.ContentAsset(
            canonical_url="https://example.test/one-embedding",
            dedupe_key="url:https://example.test/one-embedding",
            owner_user_id=uid, visibility="UNKNOWN",
            processing_status="READY",
            title="Rolling back a deploy",
            brief=json.dumps({"overview": "how to undo a kubernetes rollout"}))
        s.add(asset)
        s.flush()
        # items has a composite FK into user_memories, so the memory row has to
        # be on disk before the item that points at the content. There is no ORM
        # relationship between the two, so the flush is explicit.
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        s.add(models.Item(
            user_id=uid, url=asset.canonical_url,
            canonical_url=asset.canonical_url, content_id=asset.id,
            title="Rolling back a deploy", status="ready",
            summary="undo a kubernetes rollout",
            search_text="Rolling back a deploy undo a kubernetes rollout",
            embedding=[0.001] * 1536, embedding_model="test-model"))
        s.commit()

    calls = []
    vector = [0.002] * 1536

    async def _embed_text(value, task="document"):
        calls.append((value, task))
        return list(vector)

    monkeypatch.setattr(embedder, "embed_text", _embed_text)

    with sessions() as s:
        results, _took = asyncio.run(
            search.hybrid_search(s, uid, "rollback", limit=5))

    assert calls == [("rollback", "query")], (
        f"the query was embedded {len(calls)} times; the two candidate lists "
        f"must share one vector")
    # The chunk list needs no rows to have been produced for this to hold, so
    # the assertion above is the one that matters; this only checks the search
    # still ran end to end.
    assert isinstance(results, list)
