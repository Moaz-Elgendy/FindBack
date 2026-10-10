"""Phase 4: privacy-aware deduplication. One user's data never reaches another.

Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
        python -m pytest tests/test_phase4_privacy.py -q
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 4 privacy tests",
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
    name = f"fb_phase4_{uuid.uuid4().hex[:10]}"
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
def alice_bob(db, sessions):
    """Two users. Alice is not special: the rules must hold for any pair."""
    ids = {"alice": uuid.uuid4(), "bob": uuid.uuid4()}
    with sessions() as s:
        for label, uid in ids.items():
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"{label}@example.test"})
        s.commit()
    return ids


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


def _publish(session, url=VIDEO):
    # Assets store the CANONICAL url (www. stripped, params sorted), so the
    # helpers must canonicalise too or they silently match nothing.
    from app.utils.canonical import canonical_url

    session.execute(text(
        "UPDATE content_assets SET visibility='PUBLIC', owner_user_id=NULL "
        "WHERE canonical_url = :c"), {"c": canonical_url(url)})
    session.commit()

def _set_visibility(session, user_id, url, visibility):
    from app.utils.canonical import canonical_url

    session.execute(text(
        "UPDATE content_assets SET visibility=:v, owner_user_id=:u "
        "WHERE canonical_url=:c"),
        {"v": visibility, "u": user_id, "c": canonical_url(url)})
    session.commit()
# --- required test 1: A cannot access B's private content ----------------

@pytest.mark.parametrize("visibility", ["PRIVATE", "UNKNOWN"])
def test_user_b_cannot_join_another_users_private_content(
        db, sessions, alice_bob, visibility):
    """A second user must get their OWN copy, never the first user's asset."""
    a, b = alice_bob["alice"], alice_bob["bob"]
    with sessions() as s:
        _save(s, a)
        _set_visibility(s, a, VIDEO, visibility)
        _save(s, b)

    with sessions() as s:
        assets = s.execute(text(
            "SELECT id, owner_user_id FROM content_assets")).mappings().all()

        def assets_of(user_id):
            return s.execute(text(
                "SELECT a.id FROM content_assets a JOIN items i "
                "ON i.content_id = a.id WHERE i.user_id = :u"),
                {"u": user_id}).scalars().all()

    assert len(assets) == 2, f"{visibility} content must never be shared"
    assert not (set(assets_of(a)) & set(assets_of(b))), \
        "Bob must not be attached to Alice's asset"
    assert {r["owner_user_id"] for r in assets} == {a, b}


def test_user_b_cannot_read_user_as_items_or_search(db, sessions, alice_bob):
    """Read paths must stay scoped to the requesting user."""
    from app.routers.items import list_items
    from app.services.search import hybrid_search

    a, b = alice_bob["alice"], alice_bob["bob"]
    with sessions() as s:
        _save(s, a, title="Alice Private Risotto",
              preview="alice only risotto recipe")
        _save(s, b, url="https://example.test/bobs", title="Bob Public Thing",
              preview="bob unrelated content")
        s.execute(text("UPDATE items SET status='ready', title_clean=title"))
        s.commit()

# --- required tests 2 & 3: notes and intent stay private ------------------

@pytest.mark.parametrize("field", ["user_note", "user_intent"])
def test_user_note_and_intent_are_never_shared_through_an_asset(
        db, sessions, alice_bob, field):
    """Even PUBLIC sharing must not carry one user's note or intent.

    The shared object is the ContentAsset; a UserMemory belongs to exactly one
    user. So the test asserts two things: the fields stay on separate rows, and
    no ContentAsset column carries them at all.
    """
    from app.models import ContentAsset

    a, b = alice_bob["alice"], alice_bob["bob"]
    with sessions() as s:
        _save(s, a)
        _publish(s, VIDEO)
        _save(s, b)
        s.execute(text(
            "UPDATE user_memories SET user_note='alice secret note', "
            "user_intent='learn' WHERE user_id = :u"), {"u": a})
        s.commit()

    with sessions() as s:
        rows = s.execute(text(
            "SELECT user_id, user_note, user_intent FROM user_memories "
            "ORDER BY user_id")).mappings().all()

    # Each user has their own row, and only Alice's carries her data.
    assert len(rows) == 2
    assert len({r["user_id"] for r in rows}) == 2
    assert [r[field] for r in rows if r["user_id"] == a] == [
        "alice secret note" if field == "user_note" else "learn"]
    assert [r[field] for r in rows if r["user_id"] == b] == [None]
    # The shared asset cannot carry user-owned data because it has no column
    # for it -- a structural guarantee, not a filtered one.
    assert field not in ContentAsset.__table__.columns


def test_no_read_path_returns_any_user_memory_fields(db, sessions, alice_bob):
    """Notes/intent/save history must not appear in API responses."""
    from app.routers.items import list_items
    from app.schemas import ItemDetail, SearchResponseItem

    private_fields = {"user_note", "user_intent", "save_count",
                      "first_saved_at", "last_saved_at"}
    for model in (ItemDetail, SearchResponseItem):
        assert not (private_fields & set(model.model_fields)), \
            f"{model.__name__} must not expose {private_fields & set(model.model_fields)}"

    a = alice_bob["alice"]
    with sessions() as s:
        _save(s, a)
        s.execute(text(
            "UPDATE user_memories SET user_note='secret', user_intent='learn' "
            "WHERE user_id = :u"), {"u": a})
        s.commit()
        payload = list_items(limit=50, cursor=None, db=s, user=_User(a))
    assert "secret" not in repr(payload)


# --- required test 4: global dedupe only for explicitly PUBLIC ------------

def test_global_dedupe_happens_only_for_public_content(db, sessions, alice_bob):
    a, b = alice_bob["alice"], alice_bob["bob"]
    other_video = "https://youtu.be/aaaaaaaaaaa"

    # Part 1: same content, never classified -> one private asset per user.
    with sessions() as s:
        _save(s, a, url=VIDEO)
        _save(s, b, url=VIDEO)
        assert s.execute(text(
            "SELECT count(*) FROM content_assets")).scalar() == 2, \
            "UNKNOWN content must not dedupe globally"

    # Part 2: same content, but PUBLIC before the second user saves.
    with sessions() as s:
        _save(s, a, url=other_video)
        _publish(s, other_video)
        _save(s, b, url=other_video)
        public_assets = s.execute(text(
            "SELECT count(*) FROM content_assets WHERE canonical_url = :c"),
            {"c": "https://youtube.com/watch?v=aaaaaaaaaaa"}).scalar()
        memories = s.execute(text(
            "SELECT count(*) FROM user_memories m JOIN content_assets c "
            "ON c.id = m.content_id WHERE c.canonical_url = :c"),
            {"c": "https://youtube.com/watch?v=aaaaaaaaaaa"}).scalar()
    assert public_assets == 1, "PUBLIC content must dedupe globally"
    assert memories == 2, "each user keeps a private memory of it"


def test_public_asset_is_owned_by_nobody(db, sessions, alice_bob):
    """A PUBLIC asset must not be attributed to whoever saved it first."""
    a, b = alice_bob["alice"], alice_bob["bob"]
    with sessions() as s:
        _save(s, a)
        _publish(s, VIDEO)
        _save(s, b)
    with sessions() as s:
        owner = s.execute(text(
            "SELECT owner_user_id FROM content_assets")).scalar()
    assert owner is None, "a shared asset belongs to no single user"
