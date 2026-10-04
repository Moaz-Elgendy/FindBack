"""Phase 3: a repeat save by the same user updates, it does not add a row.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase3_repeat_save.py -q
"""
import asyncio
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 3 repeat-save tests",
)

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


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
    """A freshly migrated, empty database."""
    name = f"fb_phase3_{uuid.uuid4().hex[:10]}"
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
def no_celery(monkeypatch):
    from app.routers import ingest as ingest_module

    monkeypatch.setattr(ingest_module, "_enqueue", lambda db, item_id, content_id=None: False)


@pytest.fixture
def user(db, sessions):
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        s.commit()
    return uid


class _User:
    def __init__(self, uid):
        self.id = uid


def _save(session, user_id, url=VIDEO, title="Video", preview="preview body"):
    from app.routers.ingest import ingest
    from app.schemas import IngestRequest

    result = ingest(IngestRequest(url=url, title_hint=title, preview=preview),
                    session, _User(user_id))
    session.commit()
    return result

def _memory(session, user_id):
    row = session.execute(text(
        "SELECT save_count, first_saved_at, last_saved_at FROM user_memories "
        "WHERE user_id = :u"), {"u": user_id}).mappings().one()
    return row


# --- required test 1: repeated save updates ------------------------------

def test_repeat_saves_increment_and_never_move_first_saved_at(db, sessions, user):
    with sessions() as s:
        _save(s, user)
        after_first = _memory(s, user)
        assert after_first["save_count"] == 1
        first_saved = after_first["first_saved_at"]

        _save(s, user)
        after_second = _memory(s, user)
        assert after_second["save_count"] == 2
        assert after_second["first_saved_at"] == first_saved, \
            "first_saved_at must never change"
        assert after_second["last_saved_at"] >= after_first["last_saved_at"]

        _save(s, user)
        after_third = _memory(s, user)
        assert after_third["save_count"] == 3
        assert after_third["first_saved_at"] == first_saved

        # Still exactly one memory row and one item: an update, not an insert.
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": user}).scalar() == 1
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": user}).scalar() == 1


def test_repeat_save_returns_the_same_item(db, sessions, user):
    with sessions() as s:
        first = _save(s, user)
        again = _save(s, user)
        # Even a different URL form of the same content is the same save.
        other_form = _save(s, user, url="https://youtu.be/dQw4w9WgXcQ")
    assert str(first.id) == str(again.id) == str(other_form.id)


def test_sync_batch_replay_also_counts_as_a_save(db, sessions, user):
    from app.routers.ingest import sync_batch
    from app.schemas import SyncBatchRequest, SyncItem

    with sessions() as s:
        _save(s, user)
        assert _memory(s, user)["save_count"] == 1
        out = sync_batch(SyncBatchRequest(items=[
            SyncItem(client_id="c1", url=VIDEO)]), s, _User(user))
        s.commit()
        assert out.errors == []
        assert _memory(s, user)["save_count"] == 2


def test_unique_constraint_on_user_and_content_exists(db):
    """The (user_id, content_id) guarantee must be a real constraint."""
    with db.connect() as conn:
        contype = conn.execute(text("""
            SELECT contype FROM pg_constraint
            WHERE conrelid = 'user_memories'::regclass
              AND conname = 'user_memories_user_content_uq'
        """)).scalar()
        cols = conn.execute(text("""
            SELECT a.attname FROM pg_constraint c
            JOIN unnest(c.conkey) AS k(attnum) ON true
            JOIN pg_attribute a ON a.attrelid = c.conrelid
                               AND a.attnum = k.attnum
            WHERE c.conname = 'user_memories_user_content_uq'
            ORDER BY a.attname
        """)).scalars().all()
    assert contype == "u", "must be a UNIQUE constraint, not an index check"
    assert cols == ["content_id", "user_id"]

# --- required test 2: concurrent same-user save --------------------------

def test_concurrent_same_user_saves_count_every_save(db, sessions, user):
    """N threads saving the same content must end at exactly save_count = N.

    This is the test a read-then-write increment fails: two threads read the
    same count and both write count+1, losing one. The atomic
    INSERT .. ON CONFLICT in _record_save is what makes N land exactly.
    """
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            with sessions() as session:
                barrier.wait(timeout=30)
                _save(session, user)
        except Exception as exc:  # noqa: BLE001 - reported below
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"concurrent saves raised: {errors}"
    with sessions() as s:
        memory = _memory(s, user)
        rows = s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": user}).scalar()
        items = s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": user}).scalar()
    assert rows == 1, "a repeat save must not add a second memory row"
    assert items == 1, "a repeat save must not add a second item row"
    assert memory["save_count"] == thread_count, \
        f"every concurrent save must be counted, got {memory['save_count']}"


def test_concurrent_saves_of_different_url_forms_also_count(db, sessions, user):
    """Different URL forms are the same save, concurrently included."""
    urls = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ"]
    barrier = threading.Barrier(len(urls))
    errors = []

    def worker(url):
        try:
            with sessions() as session:
                barrier.wait(timeout=30)
                _save(session, user, url=url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(u,)) for u in urls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"concurrent saves raised: {errors}"
    with sessions() as s:
        memory = _memory(s, user)
        memories = s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": user}).scalar()
        assets = s.execute(text("SELECT count(*) FROM content_assets")).scalar()
    # Every concurrent save is counted, and there is still exactly one memory
    # row and one asset.
    assert memory["save_count"] == len(urls)
    assert memories == 1
    assert assets == 1
# --- required test 3: search must not repeat one piece of content --------

def test_search_returns_one_result_for_content_saved_more_than_once(
        db, sessions, user):
    """Two item rows for one ContentAsset must produce ONE search result.

    Repeat saves update rather than insert in the normal path, but a concurrent
    save can still leave two item rows pointing at the same asset. Search must
    collapse them so the user never sees the same memory twice.
    """
    from app.services.search import hybrid_search

    with sessions() as s:
        _save(s, user, title="Chicken Cream Mushroom Risotto",
              preview="A creamy one-pan risotto dinner")
        # Search only considers processed rows, so mark the real save ready
        # the way the worker would.
        s.execute(text(
            "UPDATE items SET status='ready', title_clean='Chicken Cream Mushroom "
            "Risotto' WHERE user_id = :u"), {"u": user})
        content_id = s.execute(text(
            "SELECT content_id FROM items WHERE user_id = :u"),
            {"u": user}).scalar()
        # A second row for the same user and same content, as a lost race would
        # leave behind. Different URL, different item id, one asset.
        s.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, title, title_clean,
                source_domain, source_type, summary, key_points, entities, tags,
                status, content_id, created_at, last_seen_at)
            VALUES (:u, 'https://example.test/dup', 'https://example.test/dup',
                'Risotto', 'Chicken Cream Mushroom Risotto', 'example.test',
                'article', 'A creamy one-pan dinner', '[]'::jsonb, '{}'::jsonb,
                ARRAY[]::text[], 'ready', :cid, now(), now())
        """), {"u": user, "cid": content_id})
        s.commit()
        matching = s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u AND status='ready' "
            "AND tsv @@ plainto_tsquery('english', 'risotto')"),
            {"u": user}).scalar()
        assert matching == 2, "precondition: two item rows match"

        results, _took = asyncio.run(
            hybrid_search(s, user, "chicken cream mushroom risotto"))

    titles = [r["row"][1] for r in results]
    assert len(results) == 1, f"search returned the same content twice: {titles}"


def test_dedupe_keeps_rows_without_a_content_id_separate():
    """Rows with NULL content_id must not all collapse into one result."""
    from app.services.search import _content_key, _dedupe_rows

    def row(item_id, content_id):
        return (item_id, "t", "s", [], None, "d", None, None, content_id, 1.0)

    null_a = row("a", None)
    null_b = row("b", None)
    same = row("c", "content-1")
    same_again = row("d", "content-1")
    other = row("e", "content-2")

    kept = _dedupe_rows([null_a, null_b, same, same_again, other])
    assert kept == [null_a, null_b, same, other]
    # Distinct content collapses to the first row seen.
    assert _content_key(same) == _content_key(same_again)
    assert _content_key(null_a) != _content_key(null_b)


def test_search_still_returns_several_distinct_results(db, sessions, user):
    """The dedupe must not collapse genuinely different memories.

    Guards the risk introduced by iterating the full ranked list instead of
    scored[:limit]: ordinary multi-result search has to keep working.
    """
    from app.services.search import hybrid_search

    titles = ["Chicken Cream Mushroom Risotto", "Bose QC45 Headphones",
              "Sourdough Loaf Guide"]
    with sessions() as s:
        _save(s, user, url="https://example.test/one", title=titles[0],
              preview="a creamy one-pan risotto dinner")
        _save(s, user, url="https://example.test/two", title=titles[1],
              preview="headphones, tested over a risotto dinner")
        _save(s, user, url="https://example.test/three", title=titles[2],
              preview="a sourdough bread baking guide")
        s.execute(text("UPDATE items SET status='ready', title_clean=title "
                      "WHERE user_id = :u"),
                  {"u": user})
        s.commit()

        # plainto_tsquery ANDs its terms, so one shared word is what makes two
        # different memories match.
        results, _took = asyncio.run(hybrid_search(s, user, "risotto"))

    found = {r["row"][1] for r in results}
    assert len(results) == 2, f"expected two distinct results, got {found}"
    assert found == {"Chicken Cream Mushroom Risotto", "Bose QC45 Headphones"}
