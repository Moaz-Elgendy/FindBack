"""Phase 11: chunks are really searched, and they keep their timestamps.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase11_chunks.py -q

The scenario the phase names: a long video says something at minute 14 that the
title never mentions. A query describing it must find the video anyway.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 11 chunk tests",
)

# The video. The title and brief know nothing about connection pooling; only the
# body of the content does, at minute 14.
VIDEO_TITLE = "Postgres Performance Deep Dive"
VIDEO_SUMMARY = "A talk about running Postgres in production."
# "at minute 14 the creator explains X" -> X is connection pool tuning.
MINUTE_14 = ("At this point in the session the presenter walks through tuning "
             "the connection pool, explaining why a pool of twenty handles "
             "four hundred concurrent requests better than no pool at all, and "
             "shows the pgbouncer configuration that made the change.")
# A long video. Two limits shape this fixture, and both are real: chunking
# splits at CHUNK_SIZE words (so the transcript must exceed it), and FETCH
# stores at most MAX_RAW_CHARS (so the minute 14 section must fit inside that
# budget too, or it is truncated away before it is ever chunked).
_FILLER = ("The presenter walks through the setup they use for every talk. ") * 40

TIMED_TRANSCRIPT = (
    "[00:00] Welcome to the talk, we will cover indexing and vacuum. "
    + _FILLER +
    "[06:30] The first section is about query planning and statistics. "
    + _FILLER +
    "[14:02] " + MINUTE_14 + " "
    + _FILLER +
    "[27:45] Closing remarks and further reading. "
    + _FILLER
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
    name = f"fb_phase11_{uuid.uuid4().hex[:10]}"
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


# --- chunking keeps the timestamps -----------------------------------------

def test_a_timestamped_transcript_produces_timed_chunks():
    from app.services.embedder import chunk_text_with_timestamps

    chunks = chunk_text_with_timestamps(TIMED_TRANSCRIPT, size=20, overlap=4)
    assert chunks, "a transcript must produce chunks"
    stamps = [c["start_timestamp"] for c in chunks if c["start_timestamp"]]
    assert stamps, "the markers in the transcript must survive"
    assert "14:02" in stamps, f"the minute 14 marker was lost: {stamps}"


def test_article_text_gets_no_invented_timestamp():
    """An article has no timeline; a chunk must not be given a fake one."""
    from app.services.embedder import chunk_text_with_timestamps

    chunks = chunk_text_with_timestamps(
        "A plain paragraph about indexes. Another plain paragraph about vacuum.",
        size=20, overlap=4)
    assert all(c["start_timestamp"] is None for c in chunks)
    assert all(c["start_seconds"] is None for c in chunks)








def test_the_marker_is_kept_out_of_the_stored_text():
    from app.services.embedder import chunk_text_with_timestamps

    chunks = chunk_text_with_timestamps("[14:02] " + MINUTE_14, size=800,
                                        overlap=0)
    assert chunks[0]["start_timestamp"] == "14:02"
    assert not chunks[0]["chunk_text"].startswith("[14:02]"), \
        "the marker is stored in its own column, not left in the text"
    assert not chunks[0]["chunk_text"].startswith("]"), \
        "the closing bracket must not be left behind as a stray character"


def test_every_chunk_of_a_section_keeps_that_sections_time():
    """A long section is several chunks, and all of them belong to its time."""
    from app.services.embedder import chunk_text_with_timestamps

    long_section = "[14:02] " + (MINUTE_14 + " ") * 40
    chunks = chunk_text_with_timestamps(long_section)
    assert len(chunks) > 1, "the section must actually be split"
    assert {c["start_timestamp"] for c in chunks} == {"14:02"}


def test_timestamps_parse_to_seconds():
    from app.services.embedder import parse_timestamp

    assert parse_timestamp("14:02") == 842
    assert parse_timestamp("00:00") == 0
    assert parse_timestamp("1:02:03") == 3723
    assert parse_timestamp("not a time") is None


# --- end to end: a real query finds the minute 14 fact ---------------------



def _words(text):
    return [w.strip(".,;:()[]").lower() for w in text.split()]


def _seed_video(db, sessions):
    """The video, processed through the real pipeline with a fake embedder."""
    import app.models as models
    from app import database as db_module
    import app.tasks as app_tasks
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": "video@example.test"})
        s.commit()

    # A tiny deterministic vectorizer: the content words of the minute 14
    # passage occupy the first dimensions, and cosine similarity is what decides
    # the match, the way a real model would behave.
    vocab = sorted(set(_words(MINUTE_14)) - {"at", "this", "of", "in", "the",
                                            "and", "to", "a", "by", "why"})
    # items.embedding is a Vector(1536): a short stub vector would be rejected by
    # the column, and the item would finish with no chunks at all.
    DIM = 1536

    def vec(text):
        words = set(_words(text))
        vector = [0.0] * DIM
        for index, word in enumerate(vocab):
            if word in words:
                vector[index] = 1.0
        return vector

    async def _embed(texts, task="document"):
        return [vec(t) for t in texts]

    async def _fetch(url, preview=""):
        return {"text": TIMED_TRANSCRIPT, "title": VIDEO_TITLE,
                "source_type": "video"}

    async def _extract(text, title="", url=""):
        return Brief(title=VIDEO_TITLE, overview=VIDEO_SUMMARY,
                     highlights=["indexing", "vacuum", "planning"],
                     topics=["postgres"], intent=["watch", "learn"],
                     structured_data={"content_type": "list",
                                      "items": ["indexing", "vacuum",
                                                "query planning"]})

    embedder.embed_many = _embed

    async def _embed_one(text, task="document"):
        return vec(text) or [0.0] * len(vocab)

    embedder.embed_text = _embed_one
    embedder.embedding_model_name = lambda: "test-model"
    fetcher.fetch_content = _fetch
    # Replaced on the module: `restore_service_modules` in conftest.py puts the
    # real one back after this test, so this cannot leak into another one.
    extractor.extract_brief = _extract
    storage.store_raw_snapshot = lambda i, p: None

    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/pg-talk",
            dedupe_key="url:https://example.test/pg-talk",
            owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        # Phase 16: items_user_content_fk requires this row to exist before an
        # item may name the asset. Real saves always create it, so the fixture
        # creates it too. Setup only -- no assertion depends on it.
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        item = models.Item(user_id=uid, url=asset.canonical_url,
                           canonical_url=asset.canonical_url,
                           title=VIDEO_TITLE, content_id=asset.id)
        s.add(item)
        s.flush()
        item_id = str(item.id)
        s.commit()

    previous = db_module._engine
    db_module._engine = db
    db_module._sessionmaker = None
    try:
        app_tasks.process_item.apply(args=(item_id,), throw=False)
    finally:
        db_module._engine = previous
        db_module._sessionmaker = None
    return uid, item_id


from sqlalchemy.orm import Session

def _search(db, uid, query):
    """Run the real search against the test database."""
    import asyncio

    from app.services import search

    with Session(bind=db) as s:
        return asyncio.run(search.hybrid_search(s, uid, query, limit=5))


QUERY = "connection pool tuning for many concurrent requests"


def test_a_query_not_in_the_title_finds_the_video(db, sessions):
    """The phase's requirement, end to end.

    The query describes the minute 14 content. Neither the title nor the brief
    mentions connection pooling, so only a chunk search can find it.
    """
    uid, item_id = _seed_video(db, sessions)
    results, _took = _search(db, uid, QUERY)

    ids = [str(r["row"][0]) for r in results]
    assert item_id in ids, "the video must be found by its content, not its title"
    top = next(r for r in results if str(r["row"][0]) == item_id)
    assert "14:02" in top["match_reason"], \
        f"the answer should point at the moment: {top['match_reason']}"



def test_the_chunk_that_matched_really_carries_the_timestamp(db, sessions):
    _seed_video(db, sessions)
    with db.connect() as conn:
        rows = conn.execute(text(
            "SELECT chunk_idx, chunk_text, start_timestamp, start_seconds "
            "FROM chunks ORDER BY start_seconds NULLS LAST")).fetchall()
    assert rows, "the video produced no chunks"
    timed = [r for r in rows if r[2]]
    assert timed, f"no chunk kept a timestamp: {[(r[0], r[2]) for r in rows]}"
    pool = [r for r in timed if "connection pool" in r[1].lower()]
    assert pool, "the minute 14 content is not in any chunk"
    assert pool[0][2] == "14:02"
    assert pool[0][3] == 842


def test_chunk_search_finds_it_even_with_no_item_embedding(db, sessions):
    """Chunks stand on their own: an item with no vector is still searchable."""
    import asyncio

    from sqlalchemy.orm import Session

    from app.services import search

    uid, item_id = _seed_video(db, sessions)
    with db.connect() as conn:
        conn.execute(text("UPDATE items SET embedding = NULL"))

    with Session(bind=db) as s:
        rows = asyncio.run(search.chunk_search(s, uid, QUERY))
    assert rows, "a chunk search must work with no item embedding"
    assert str(rows[0][0]) == item_id
    assert rows[0][12] == "14:02", "the winning chunk knows when it was said"


def test_chunk_search_respects_the_owner(db, sessions):
    """Another user's chunks must never leak: the Phase 4 privacy rule."""
    import asyncio

    from sqlalchemy.orm import Session

    from app.services import search

    uid, _item_id = _seed_video(db, sessions)
    other = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": other, "e": "other@example.test"})
        s.commit()

    with Session(bind=db) as s:
        rows = asyncio.run(search.chunk_search(s, other, QUERY))
    assert rows == [], "a chunk search crossed a user boundary"


def test_a_chunk_hit_says_what_matched(db, sessions):
    """The reason a chunk won is quoted back, with the moment it was said.

    Phase 12 changed the wording: the reason now leads with the matched terms
    and marks where they were found, rather than starting "At 14:02:".
    """
    uid, _item_id = _seed_video(db, sessions)
    results, _took = _search(db, uid, QUERY)
    reasons = [r["match_reason"] for r in results]
    assert any("14:02" in r for r in reasons), \
        f"expected the matched moment in the reason: {reasons}"
    assert any("pool" in r for r in reasons), \
        f"expected the matched words in the reason: {reasons}"
