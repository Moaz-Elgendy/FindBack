"""Regression tests for the code audit.

Each test pins one defect that was found by reading and then reproduced:

    * `_ranks` kept the WORST chunk rank of an item instead of the best
    * `canonical_url` mangled any bare host that merely starts with "http"
    * `get_current_user` was `async def` but queries the database synchronously,
      so every authenticated request stalled the event loop
    * `hybrid_search` ran five synchronous SQL queries on the event loop
    * one failed search query aborted the transaction for every query after it
    * `GET /items` crashed on limit<=0, mis-paged for limit>100, skipped rows
      with equal timestamps and restarted from page one when the cursor row
      had been deleted
    * `GET /items/{id}` returned 500 for an id that is not a UUID
    * `/sync/batch` accepted an unbounded batch
    * Celery workers prefetched four jobs each and had no run-time ceiling
"""
import asyncio
import inspect
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import database as db_module
from app.models import Item, User

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


# --------------------------------------------------------------------------- #
# Pure unit tests                                                              #
# --------------------------------------------------------------------------- #

def test_ranks_keep_the_best_rank_of_an_item_with_several_chunks():
    from app.services import search

    rows = [("a",), ("b",), ("a",), ("c",), ("b",)]
    assert search._ranks(rows) == {"a": 1, "b": 2, "c": 4}


@pytest.mark.parametrize("raw,expected", [
    ("httpbin.org/get", "https://httpbin.org/get"),
    ("httpstat.us/200", "https://httpstat.us/200"),
    ("http://example.com/a/", "https://example.com/a"),
    ("https://www.example.com/a?utm_source=x&b=2&a=1",
     "https://example.com/a?a=1&b=2"),
    ("example.com", "https://example.com/"),
])
def test_canonical_url_only_treats_a_real_scheme_as_a_scheme(raw, expected):
    from app.utils.canonical import canonical_url

    assert canonical_url(raw) == expected


def test_the_auth_dependency_runs_in_the_threadpool():
    """An `async def` dependency runs ON the event loop, and this one issues
    synchronous SQLAlchemy queries, so it froze every other request while it
    waited on Postgres. A plain `def` is dispatched to a worker thread."""
    from app.auth import get_current_user

    assert not inspect.iscoroutinefunction(get_current_user)


def test_search_does_its_database_work_off_the_event_loop(monkeypatch):
    from app.services import embedder, search

    async def fake_embed(text_, task="document"):
        return [0.0] * 4

    def slow(*_a, **_k):
        time.sleep(0.4)  # a blocking query
        return []

    monkeypatch.setattr(embedder, "embed_text", fake_embed)
    monkeypatch.setattr(search, "correct_tag_terms", lambda db, uid, terms: terms)
    monkeypatch.setattr(search, "lexical_search", slow)
    for name in ("vector_search", "chunk_lexical_search", "note_search"):
        monkeypatch.setattr(search, name, lambda *a, **k: [])
    monkeypatch.setattr(search, "_chunk_vector_rows", lambda *a, **k: [])

    async def scenario():
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        await search.hybrid_search(object(), uuid.uuid4(), "anything")
        stop = True
        await task
        return ticks

    # 0.4 s of blocking SQL: if it ran on the loop the ticker got ~0 turns.
    assert asyncio.run(scenario()) >= 8


def test_sync_batch_is_bounded():
    from app.schemas import SyncBatchRequest, SyncItem

    one = {"client_id": "c", "url": "https://example.com/x"}
    SyncBatchRequest(items=[SyncItem(**one)] * 100)
    with pytest.raises(ValidationError):
        SyncBatchRequest(items=[SyncItem(**one)] * 101)


def test_celery_workers_take_one_job_at_a_time_and_have_a_ceiling():
    from app.celery_app import celery

    assert celery.conf.worker_prefetch_multiplier == 1
    assert 0 < celery.conf.task_soft_time_limit < celery.conf.task_time_limit


# --------------------------------------------------------------------------- #
# Database-backed tests                                                        #
# --------------------------------------------------------------------------- #

pytestmark_db = pytest.mark.skipif(not TEST_DATABASE_URL,
                                   reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def engine():
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    name = f"fb_audit_{uuid.uuid4().hex[:10]}"
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
def sessions(engine):
    return sessionmaker(bind=engine)


def _user(sessions):
    with sessions() as s:
        user = User(email=f"{uuid.uuid4().hex}@example.com")
        s.add(user)
        s.commit()
        return user.id


def _item(s, uid, n, created_at):
    item = Item(user_id=uid, url=f"https://example.com/{n}",
                canonical_url=f"https://example.com/{n}", title=f"t{n}",
                status="ready", created_at=created_at)
    s.add(item)
    s.commit()
    return str(item.id)


@pytest.fixture
def api(sessions):
    """The real app with the identity pinned to one user."""
    from app.auth import get_current_user
    from app.main import app

    holder = SimpleNamespace(uid=None)

    def get_session():
        with sessions() as s:
            yield s

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[db_module.get_db] = get_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder.uid)

    def call(method, path, **kwargs):
        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as c:
                return await c.request(method, path, **kwargs)
        return asyncio.run(go())

    call.holder = holder
    try:
        yield call
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _walk(api, **params):
    """Follow next_cursor to the end and return every id seen, in order."""
    seen, cursor = [], None
    for _ in range(20):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        response = api("GET", "/api/v1/items", params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        seen += [row["id"] for row in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return seen
    raise AssertionError("pagination never terminated")


@pytestmark_db
def test_items_with_the_same_timestamp_are_not_skipped(sessions, api):
    uid = _user(sessions)
    api.holder.uid = uid
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as s:
        ids = {_item(s, uid, n, moment) for n in range(5)}
    seen = _walk(api, limit=2)
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))


@pytestmark_db
def test_a_deleted_cursor_row_does_not_restart_the_list(sessions, api):
    uid = _user(sessions)
    api.holder.uid = uid
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as s:
        ids = [_item(s, uid, n, base + timedelta(minutes=n)) for n in range(5)]
    newest_first = list(reversed(ids))

    first = api("GET", "/api/v1/items", params={"limit": 2}).json()
    assert [r["id"] for r in first["items"]] == newest_first[:2]
    with sessions() as s:  # the row the cursor points at is deleted meanwhile
        s.execute(text("DELETE FROM items WHERE id = :i"), {"i": newest_first[1]})
        s.commit()
    second = api("GET", "/api/v1/items",
                 params={"limit": 2, "cursor": first["next_cursor"]})
    assert second.status_code == 200
    assert [r["id"] for r in second.json()["items"]] == newest_first[2:4]


@pytestmark_db
@pytest.mark.parametrize("limit", [0, -5, 101, 5000])
def test_items_limit_is_validated_not_crashed_or_silently_truncated(
        sessions, api, limit):
    api.holder.uid = _user(sessions)
    assert api("GET", "/api/v1/items", params={"limit": limit}).status_code == 422


@pytestmark_db
def test_an_item_id_that_is_not_a_uuid_is_a_422(sessions, api):
    api.holder.uid = _user(sessions)
    assert api("GET", "/api/v1/items/not-a-uuid").status_code == 422
    assert api("DELETE", "/api/v1/items/not-a-uuid").status_code == 422
    missing = api("GET", f"/api/v1/items/{uuid.uuid4()}")
    assert missing.status_code == 404


@pytestmark_db
def test_one_failed_search_query_does_not_poison_the_next(sessions):
    from app.services import search

    uid = _user(sessions)
    with sessions() as s:
        row = Item(user_id=uid, url="https://example.com/v",
                   canonical_url="https://example.com/v", status="ready",
                   embedding=[0.1] * 1536)
        s.add(row)
        s.commit()
        # 3 dimensions against a vector(1536) column: Postgres raises once it
        # has a row to compare against.
        assert search.vector_search(s, uid, [0.1, 0.2, 0.3]) == []
        # Before the fix this raised InFailedSqlTransaction.
        assert s.execute(text("SELECT 1")).scalar() == 1
        assert search.lexical_search(s, uid, "x", ["x"]) == []
