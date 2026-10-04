"""Phase 16: a user can reach their own data and nothing else.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase16_multitenant.py -q

Every test here drives the real app over HTTP against a real PostgreSQL, with
two real users. Unit-level isolation tests already exist for the services
(phases 4, 12 and 13); what those cannot show is whether a ROUTE leaks, because
a route is exactly where an id from the URL reaches a query that forgets the
user. So these tests change ids in URLs and in bodies and assert the status
code.

Also covered, because the phase asks for the guarantee to hold at the database
level too, not only in application code: a composite foreign key and NOT NULL /
UNIQUE constraints, asserted by really trying to insert rows that must fail.

`fastapi.testclient.TestClient` cannot be used here: starlette 0.36.3 calls
`httpx.Client(app=...)`, which httpx 0.28.1 removed. `httpx.ASGITransport` is
the supported path on the installed versions, so requests go through the real
ASGI app either way.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app import database as db_module

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 16 multi-tenant tests",
)


# --- the HTTP client -------------------------------------------------------

import httpx  # noqa: E402

from app.auth import get_current_user  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402


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
    """A throwaway database at head, built by the real Alembic chain."""
    name = f"fb_phase16_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = create_engine(url, pool_pre_ping=True)
    try:
        cfg = db_module.alembic_config()
        cfg.set_main_option("sqlalchemy.url", url)
        from alembic import command
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
def client(db, sessions):
    """An HTTP client bound to the throwaway database.

    Authentication is overridden rather than faked per request: a real signed
    JWT would test `decode_access_token`, which is not what this phase is about.
    The unauthenticated tests deliberately use NO override at all.
    """
    original = dict(app.dependency_overrides)
    # Bound to locals first: a class body does not see the enclosing function's
    # names, so `sessions = sessions` inside one would pick up the module-level
    # FIXTURE instead, and every call would be a fixture called directly.
    engine = db
    session_factory = sessions

    # app.database caches a module-level engine and sessionmaker on first use.
    # `GET /health` calls SessionLocal() directly, so exercising the real app
    # caches a sessionmaker bound to DATABASE_URL -- and any later test that
    # repoints `_engine` at its own database then finds the cache stale and its
    # SQL lands in the wrong place. Save and clear both, and restore them after,
    # so this file leaves no global state behind.
    saved_engine = db_module._engine
    saved_sessionmaker = db_module._sessionmaker
    db_module._engine = engine
    db_module._sessionmaker = session_factory

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    class Harness:
        def __init__(self):
            self.db = engine
            self.sessions = session_factory

        def as_user(self, user_id):
            def _resolve():
                return session_factory().query(_user_model()).filter(
                    _user_model().id == user_id).one()
            app.dependency_overrides[get_current_user] = _resolve

        def anonymous(self):
            app.dependency_overrides.pop(get_current_user, None)

        def request(self, method, path, **kwargs):
            async def _send():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport,
                                             base_url="http://test") as c:
                    return await c.request(method, path, **kwargs)
            return asyncio.run(_send())

    try:
        yield Harness()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)
        db_module._engine = saved_engine
        db_module._sessionmaker = saved_sessionmaker


def _user_model():
    from app.models import User
    return User


# --- fixtures for two real users ------------------------------------------

def _make_user(sessions, email, subject=None):
    with sessions() as s:
        s.execute(text("""
            INSERT INTO users (email, auth_subject, auth_provider)
            VALUES (:e, :s, 'test')
        """), {"e": email, "s": subject})
        s.commit()
        return s.execute(text(
            "SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()


def _ready_item(sessions, user_id, url, asset_visibility="UNKNOWN"):
    """A processed item for this user, with the memory row Phase 16 requires.

    Each url is unique per user, so two users saving the SAME url here would
    share one asset -- which is what `test_b_does_not_join_a_private_asset...`
    and the PUBLIC-asset tests build deliberately instead.
    """
    import app.models as models

    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url=url, dedupe_key=f"url:{url}",
            owner_user_id=user_id, visibility=asset_visibility,
            processing_status="READY", title=f"The title of {url}")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=user_id, content_id=asset.id))
        s.flush()
        item = models.Item(
            user_id=user_id, url=url, canonical_url=url,
            content_id=asset.id, title=f"The title of {url}",
            title_clean=f"The title of {url}",
            summary=f"A memorable summary about {url}",
            # SHARED_TOKEN is in BOTH users' searchable documents on purpose:
            # a leak test is only meaningful when the query really matches
            # rows on both sides. searchablemarker is per-item, for tests that
            # need to find exactly one row.
            search_text=f"A memorable summary sharedtoken {url} "
                        f"searchablemarker",
            status="ready")
        s.add(item)
        s.flush()
        result = str(item.id), str(asset.id)
        s.commit()
    return result


# A word that is in every fixture item's search_text, so a search for it must
# match rows belonging to BOTH users. Without this a "no leak" assertion could
# pass simply because the query matched nothing.
SHARED_TOKEN = "sharedtoken"


@pytest.fixture
def two_users(sessions):
    """A and B, each with one saved item and one note. Every leak test needs
    both sides to exist, so it is built once and shared."""
    a = _make_user(sessions, "a@example.test", f"sub-a-{uuid.uuid4().hex[:8]}")
    b = _make_user(sessions, "b@example.test", f"sub-b-{uuid.uuid4().hex[:8]}")
    a_item, a_asset = _ready_item(sessions, a, "https://example.test/alpha")
    b_item, b_asset = _ready_item(sessions, b, "https://example.test/beta")
    with sessions() as s:
        s.execute(text("""
            UPDATE user_memories SET user_note = 'A private note',
                                    user_intent = 'project'
             WHERE user_id = :u
        """), {"u": a})
        s.commit()
    return {"a": a, "b": b, "a_item": a_item, "b_item": b_item,
            "a_asset": a_asset, "b_asset": b_asset}


# --- 1. another user's item, by direct id ---------------------------------

def test_b_cannot_read_another_users_item_by_id(client, two_users):
    """The id alone must not be enough. This is the whole Phase 16 subject."""
    client.as_user(two_users["a"])
    ok = client.request("GET", f"/api/v1/items/{two_users['a_item']}")
    assert ok.status_code == 200, ok.text

    client.as_user(two_users["b"])
    denied = client.request("GET", f"/api/v1/items/{two_users['a_item']}")
    assert denied.status_code == 404, (
        f"expected 404, got {denied.status_code}: {denied.text}")
    assert two_users["a_item"] not in denied.text


def test_b_cannot_delete_another_users_item_by_id(client, two_users):
    client.as_user(two_users["b"])
    denied = client.request("DELETE", f"/api/v1/items/{two_users['a_item']}")
    assert denied.status_code == 404, denied.text

    # The real proof is that A's row is still there.
    client.as_user(two_users["a"])
    still = client.request("GET", f"/api/v1/items/{two_users['a_item']}")
    assert still.status_code == 200, "B's delete removed A's item"


def test_a_cannot_update_or_delete_b_s_user_memory_by_id(client, two_users):
    """UserMemory has no update route by item id, so this goes through the
    route that does exist and the only other entry point: a direct id."""
    client.as_user(two_users["a"])
    denied = client.request("DELETE",
                            f"/api/v1/memories/{two_users['b_asset']}")
    assert denied.status_code == 404, denied.text

    with client.sessions() as s:
        note = s.execute(text(
            "SELECT user_note FROM user_memories WHERE user_id = :u"),
            {"u": two_users["b"]}).scalar()
    assert note is None, "A cleared B's note"


# --- 2. user_note and user_intent -----------------------------------------

def test_b_cannot_read_a_s_note_or_intent(client, two_users):
    client.as_user(two_users["b"])
    denied = client.request("GET", f"/api/v1/memories/{two_users['a_asset']}")
    assert denied.status_code == 404, denied.text
    assert "A private note" not in denied.text
    assert "project" not in denied.text


def test_b_cannot_write_to_a_s_memory_by_content_id(client, two_users):
    client.as_user(two_users["b"])
    for method in ("PATCH",):
        denied = client.request(
            method, f"/api/v1/memories/{two_users['a_asset']}",
            json={"note": "overwritten by B", "intent": "research"})
    assert denied.status_code == 404, denied.text

    with client.sessions() as s:
        row = s.execute(text(
            "SELECT user_note, user_intent FROM user_memories "
            "WHERE user_id = :u"), {"u": two_users["a"]}).one()
    assert row[0] == "A private note", "B overwrote A's note"
    assert row[1] == "project", "B changed A's intent"


def test_a_note_is_never_searchable_from_another_user(client, two_users):
    """Even a word that appears ONLY in A's note must not surface A's memory
    when B searches for it."""
    client.as_user(two_users["a"])
    found = client.request("GET", "/api/v1/search?q=searchablemarker")
    assert found.status_code == 200, found.text
    assert any(r["id"] == two_users["a_item"]
               for r in found.json()["results"]), found.text

    client.as_user(two_users["b"])
    leaked = client.request("GET", "/api/v1/search?q=searchablemarker")
    assert leaked.status_code == 200, leaked.text
    ids = {str(r["id"]) for r in leaked.json()["results"]}
    assert two_users["a_item"] not in ids, "A's memory surfaced in B's search"
    assert "A private note" not in leaked.text


# --- 3. a PRIVATE ContentAsset --------------------------------------------

def test_b_cannot_read_a_private_asset_by_content_id(client, two_users):
    client.as_user(two_users["b"])
    denied = client.request("GET", f"/api/v1/memories/{two_users['a_asset']}")
    assert denied.status_code == 404, denied.text
    assert "The title of https://example.test/alpha" not in denied.text


def test_b_cannot_reach_a_private_asset_through_a_shared_one(
        client, sessions, two_users):
    """The hard case: A and B both hold a PUBLIC asset, and A also holds a
    PRIVATE one. B's legitimate access to the shared row must not become a way
    to walk to A's private row."""
    import app.models as models

    shared = models.ContentAsset(
        canonical_url="https://example.test/shared",
        dedupe_key="url:https://example.test/shared",
        visibility="PUBLIC", owner_user_id=None,
        processing_status="READY", title="A public talk")
    private = models.ContentAsset(
        canonical_url="https://example.test/a-secret",
        dedupe_key="url:https://example.test/a-secret",
        visibility="PRIVATE", owner_user_id=two_users["a"],
        processing_status="READY", title="A private talk")
    with sessions() as s:
        s.add(shared)
        s.add(private)
        s.flush()
        for uid in (two_users["a"], two_users["b"]):
            s.add(models.UserMemory(user_id=uid, content_id=shared.id))
        s.flush()
        shared_id, private_id = str(shared.id), str(private.id)
        s.commit()

    client.as_user(two_users["b"])
    allowed = client.request("GET", f"/api/v1/memories/{shared_id}")
    assert allowed.status_code == 200, allowed.text

    denied = client.request("GET", f"/api/v1/memories/{private_id}")
    assert denied.status_code == 404, denied.text
    assert "A private talk" not in denied.text


def test_b_does_not_join_a_private_asset_when_saving_the_same_url(
        client, two_users):
    """PRODUCT.md rule 3: a duplicate URL does not mean the content is safe to
    share. B saving A's private URL must produce B's own copy, never a join."""
    url = "https://example.test/alpha"
    client.as_user(two_users["b"])
    response = client.request("POST", "/api/v1/ingest", json={"url": url})
    assert response.status_code == 200, response.text
    new_item = response.json()["id"]

    with client.sessions() as s:
        row = s.execute(text("""
            SELECT i.content_id, a.owner_user_id, a.visibility
            FROM items i JOIN content_assets a ON a.id = i.content_id
            WHERE i.id = :i
        """), {"i": new_item}).one()
    assert str(row[1]) == str(two_users["b"]), (
        "B's save joined an asset owned by A")
    assert row[2] != "PUBLIC", "B's save joined a PUBLIC asset it never shared"


# --- 4. search ------------------------------------------------------------

def test_search_returns_only_the_callers_own_results(client, two_users):
    """Both users' items carry SHARED_TOKEN, so a query for it matches rows on
    both sides. That is what makes this a leak test rather than a test of an
    empty result set: each user must see their own row and not the other's."""
    client.as_user(two_users["a"])
    results = client.request(
        "GET", f"/api/v1/search?q={SHARED_TOKEN}").json()["results"]
    ids = {str(r["id"]) for r in results}
    assert two_users["a_item"] in ids, (
        f"A's own item was not found, so the test proves nothing: {results}")
    assert two_users["b_item"] not in ids, "A's search returned B's item"

    client.as_user(two_users["b"])
    results_b = client.request(
        "GET", f"/api/v1/search?q={SHARED_TOKEN}").json()["results"]
    ids_b = {str(r["id"]) for r in results_b}
    assert two_users["b_item"] in ids_b, (
        f"B's own item was not found, so the test proves nothing: {results_b}")
    assert two_users["a_item"] not in ids_b, "B's search returned A's item"
    assert not (ids & ids_b), "the two users received the same result set"


def test_search_never_returns_a_shared_asset_row_from_another_user(
        client, sessions, two_users):
    """One PUBLIC asset, two users, one item each. Both items hold the same
    shared token, so the query matches both -- and the shared ContentAsset must
    not collapse them into one result that either user could read the other's
    side of."""
    import app.models as models

    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/talk",
            dedupe_key="url:https://example.test/talk",
            visibility="PUBLIC", owner_user_id=None,
            processing_status="READY", title="A public talk")
        s.add(asset)
        s.flush()
        asset_id = asset.id
        items = {}
        for label, uid in (("a", two_users["a"]), ("b", two_users["b"])):
            # The memory row first: items_user_content_fk requires it to exist
            # before an item may name the asset, which is the order a real save
            # uses too.
            s.add(models.UserMemory(user_id=uid, content_id=asset_id))
            s.flush()
            item = models.Item(
                user_id=uid, url=asset.canonical_url,
                canonical_url=asset.canonical_url,
                content_id=asset_id, title="A public talk",
                search_text=f"sharedtoken public talk for {label}",
                status="ready")
            s.add(item)
            s.flush()
            items[label] = str(item.id)
        s.commit()

    for label, uid in (("a", two_users["a"]), ("b", two_users["b"])):
        client.as_user(uid)
        results = client.request(
            "GET", f"/api/v1/search?q={SHARED_TOKEN}").json()["results"]
        ids = {str(r["id"]) for r in results}
        assert items[label] in ids, (
            f"{label}'s own item is missing, so this proves nothing: {results}")
        other = items["b" if label == "a" else "a"]
        assert other not in ids, (
            f"{label}'s search returned the other user's row for a shared asset")


def test_search_leaks_nothing_about_a_deleted_memory(client, two_users):
    client.as_user(two_users["a"])
    client.request("DELETE", f"/api/v1/items/{two_users['a_item']}")
    after = client.request("GET", "/api/v1/search?q=searchablemarker")
    assert after.status_code == 200, after.text
    ids = {str(r["id"]) for r in after.json()["results"]}
    assert two_users["a_item"] not in ids, "a deleted item is still searchable"


# --- 5. id manipulation in the body as well as the URL --------------------

def test_a_client_supplied_user_id_is_ignored(client, two_users):
    """A forged user_id in the request body must not decide the owner.

    The save request model has no user field and Pydantic ignores unknown keys,
    so the request succeeds and the row belongs to the CALLER. The assertion is
    on the owner, not on the status code: the property that matters is that A's
    id in B's body has no effect.
    """
    client.as_user(two_users["b"])
    response = client.request(
        "POST", "/api/v1/ingest",
        json={"url": "https://example.test/forged",
              "user_id": str(two_users["a"]),
              "content_id": str(two_users["a_asset"])})
    assert response.status_code == 200, response.text
    new_item = response.json()["id"]
    assert new_item != two_users["a_item"], "the forged id returned A's item"

    with client.sessions() as s:
        row = s.execute(text("""
            SELECT user_id, content_id FROM items WHERE id = :i
        """), {"i": new_item}).one()
        assert str(row[0]) == str(two_users["b"]), (
            "a user_id in the request body decided the owner")
        assert str(row[1]) != str(two_users["a_asset"]), (
            "a content_id in the request body attached A's private asset")
        assert s.execute(text("""
            SELECT count(*) FROM user_memories
             WHERE user_id = :u AND content_id = :c
        """), {"u": two_users["b"], "c": two_users["a_asset"]}).scalar() == 0, (
            "B gained a memory of A's asset")

    # And A's own library is untouched by the attempt.
    client.as_user(two_users["a"])
    assert client.request(
        "GET", f"/api/v1/items/{two_users['a_item']}").status_code == 200


def test_a_sync_batch_never_resolves_to_another_users_item(client, two_users):
    """A replayed offline save is looked up per user, so B syncing A's URL must
    resolve to B's OWN save, or to a new one -- never to A's item id.

    B already has a save of `beta`, so B syncing `alpha` legitimately creates a
    second save rather than matching an existing one. What matters is that the
    returned id is B's and the new row is owned by B.
    """
    client.as_user(two_users["b"])
    response = client.request("POST", "/api/v1/sync/batch", json={"items": [
        {"client_id": "c1", "url": "https://example.test/alpha",
         "user_id": str(two_users["a"]),
         "content_id": str(two_users["a_asset"])},
    ]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mapped"], body
    resolved = body["mapped"][0]["id"]
    assert resolved != two_users["a_item"], (
        "B's sync batch returned A's item id")

    with client.sessions() as s:
        owner = s.execute(text(
            "SELECT user_id FROM items WHERE id = :i"),
            {"i": resolved}).scalar()
        assert str(owner) == str(two_users["b"]), (
            "B's sync batch produced an item owned by somebody else")
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 1, (
            "B's sync batch created an item for A")


# --- 6. deleted content ---------------------------------------------------

def test_a_deleted_item_is_gone_from_every_read_path(client, two_users):
    client.as_user(two_users["a"])
    assert client.request(
        "DELETE", f"/api/v1/items/{two_users['a_item']}").status_code == 204

    assert client.request(
        "GET", f"/api/v1/items/{two_users['a_item']}").status_code == 404
    listed = client.request("GET", "/api/v1/items").json()["items"]
    assert two_users["a_item"] not in {str(i["id"]) for i in listed}
    found = client.request("GET", "/api/v1/search?q=searchablemarker")
    assert two_users["a_item"] not in {str(r["id"])
                                       for r in found.json()["results"]}


def test_a_cleared_note_is_unreadable(client, two_users):
    client.as_user(two_users["a"])
    cleared = client.request(
        "DELETE", f"/api/v1/memories/{two_users['a_asset']}")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["note"] is None
    assert cleared.json()["intent"] is None
    reread = client.request("GET", f"/api/v1/memories/{two_users['a_asset']}")
    assert reread.json()["note"] is None, "the note came back"


def test_deleting_a_save_removes_both_rows_and_leaves_others_alone(
        client, sessions, two_users):
    """Q3: the item goes first, so the FK is satisfied, and one user's delete
    must not touch anybody else's rows."""
    from app.services import retention

    b_before = sessions().query(_item_model()).filter(
        _item_model().user_id == two_users["b"]).count()

    with sessions() as s:
        counts = retention.delete_save(s, two_users["a"],
                                       uuid.UUID(two_users["a_asset"]))
    assert counts["items"] == 1, counts
    assert counts["user_memories"] == 1, counts

    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 0, "A's item survived"
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 0, "A's memory survived"
        assert s.query(_item_model()).filter(
            _item_model().user_id == two_users["b"]).count() == b_before, \
            "deleting A's save changed B's rows"


def _item_model():
    from app.models import Item
    return Item


def test_a_failed_delete_rolls_back_completely(client, sessions, two_users):
    """Both DELETEs share one transaction, so a failure in the second must not
    leave the first one applied.

    The failure is provoked through the database rather than by patching the
    module, so what is under test is the transaction, not the patch.
    """
    before = sessions().query(_item_model()).filter(
        _item_model().user_id == two_users["a"]).count()
    assert before == 1

    class FailingSession:
        """Delegates everything, then fails the way a dead connection would --
        after the first DELETE has already been sent."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, *args, **kwargs):
            statement = str(args[0])
            if "DELETE FROM user_memories" in statement:
                raise OperationalError(
                    "connection lost mid-delete", None, Exception("boom"))
            return self._inner.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    failing = FailingSession(sessions())
    with pytest.raises(OperationalError):
        retention_delete(failing, two_users["a"],
                         uuid.UUID(two_users["a_asset"]))
    failing.close()

    with sessions() as s:
        assert s.query(_item_model()).filter(
            _item_model().user_id == two_users["a"]).count() == 1, (
            "a failed delete left the item removed")
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 1, (
            "a failed delete left the memory removed")


def retention_delete(session, user_id, content_id):
    from app.services import retention
    return retention.delete_save(session, user_id, content_id)


def test_the_fk_refuses_deleting_a_memory_while_an_item_points_at_it(
        client, sessions, two_users):
    """No ON DELETE CASCADE, on purpose. The database must refuse rather than
    silently take the item rows (and the derived text in them) with it."""
    with sessions() as s:
        with pytest.raises(IntegrityError):
            s.execute(text(
                "DELETE FROM user_memories WHERE user_id = :u"),
                {"u": two_users["a"]})
            s.commit()
        s.rollback()
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 1, "the item was deleted anyway"


# --- 7. requests without valid authentication -----------------------------

PROTECTED = [
    ("GET", "/api/v1/items"),
    ("GET", "/api/v1/search?q=anything"),
    ("POST", "/api/v1/ingest"),
    ("POST", "/api/v1/sync/batch"),
    ("GET", "/api/v1/memories/00000000-0000-0000-0000-000000000000"),
]


@pytest.mark.parametrize("method,path", PROTECTED,
                         ids=[f"{m} {p.split('?')[0]}" for m, p in PROTECTED])
def test_no_credentials_is_rejected(client, method, path):
    client.anonymous()
    response = client.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == 401, f"{response.status_code}: {response.text}"


@pytest.mark.parametrize("header", ["", "Bearer", "Basic abc", "Bearer ",
                                    "Bearer not-a-jwt", "bearer"])
def test_a_malformed_authorization_header_is_rejected(client, header):
    """A real signature check happens in decode_access_token, which is
    unchanged; what matters here is that nothing malformed gets through."""
    client.anonymous()
    response = client.request("GET", "/api/v1/items",
                              headers={"Authorization": header})
    assert response.status_code == 401, f"{response.status_code}: {response.text}"


def test_a_token_signed_with_another_secret_is_rejected(client, monkeypatch):
    """The signature really is verified: a token minted with a different secret
    must not authenticate, whoever it claims to be."""
    from jose import jwt

    client.anonymous()
    monkeypatch.setenv("API_SECRET_KEY", "the-real-secret-for-this-test")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    forged = jwt.encode({"sub": "sub-forged", "email": "a@example.test",
                         "aud": "authenticated"}, "the-wrong-secret",
                        algorithm="HS256")
    response = client.request("GET", "/api/v1/items",
                              headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401, f"{response.status_code}: {response.text}"


def test_health_needs_no_credentials_and_leaks_no_user_data(client):
    client.anonymous()
    response = client.request("GET", "/health")
    assert response.status_code == 200, response.text
    body = response.text
    assert "example.test" not in body, "health echoed a user address"
    assert "private note" not in body


# --- 8. database-level guarantees -----------------------------------------

def test_the_db_refuses_an_item_pointing_at_content_the_user_never_saved(
        sessions):
    """The composite FK, asserted by trying the forbidden write."""
    import app.models as models

    owner = _make_user(sessions, "fk-owner@example.test",
                       f"sub-fk-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/fk-private",
            dedupe_key="url:https://example.test/fk-private",
            owner_user_id=owner, visibility="PRIVATE")
        s.add(asset)
        s.flush()
        asset_id = asset.id
        s.commit()

    # A different user points their own item at the owner's PRIVATE asset,
    # with no memory row of their own. Exactly the leak this FK exists to stop.
    intruder = _make_user(sessions, "fk-intruder@example.test",
                          f"sub-intruder-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        s.add(models.Item(
            user_id=intruder, url="https://example.test/fk-private",
            canonical_url="https://example.test/fk-private",
            content_id=asset_id, status="pending"))
        with pytest.raises(IntegrityError) as caught:
            s.commit()
        assert "items_user_content_fk" in str(caught.value), caught.value
        s.rollback()


def test_the_fk_allows_an_item_when_the_users_own_memory_exists(sessions):
    """The complement, so the FK is not simply rejecting everything."""
    import app.models as models

    uid = _make_user(sessions, "fk-ok@example.test",
                     f"sub-ok-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/fk-ok",
            dedupe_key="url:https://example.test/fk-ok",
            owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        s.add(models.Item(
            user_id=uid, url="https://example.test/fk-ok",
            canonical_url="https://example.test/fk-ok",
            content_id=asset.id, status="pending"))
        s.commit()
        assert s.query(models.Item).filter(
            models.Item.user_id == uid).count() == 1


def test_an_item_with_no_content_id_is_still_allowed(sessions):
    """Legacy shape: a composite FK is satisfied when any column is NULL."""
    import app.models as models

    uid = _make_user(sessions, "legacy@example.test",
                     f"sub-legacy-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        s.add(models.Item(
            user_id=uid, url="https://example.test/legacy",
            canonical_url="https://example.test/legacy",
            content_id=None, status="ready"))
        s.commit()
        assert s.query(models.Item).filter(
            models.Item.user_id == uid).count() == 1


def test_a_memory_row_must_name_a_user_and_an_asset(sessions):
    """NOT NULL on both columns, plus their foreign keys."""
    import app.models as models

    uid = _make_user(sessions, "notnull@example.test",
                     f"sub-nn-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        with pytest.raises(IntegrityError):
            s.add(models.UserMemory(user_id=None, content_id=uuid.uuid4()))
            s.commit()
        s.rollback()
        with pytest.raises(IntegrityError):
            s.add(models.UserMemory(user_id=uid, content_id=None))
            s.commit()
        s.rollback()
        with pytest.raises(IntegrityError):
            s.add(models.UserMemory(user_id=uid, content_id=uuid.uuid4()))
            s.commit()
        s.rollback()


def test_one_user_cannot_have_two_memories_of_the_same_content(sessions):
    """The UNIQUE(user_id, content_id) that also makes the FK possible."""
    import app.models as models

    uid = _make_user(sessions, "dupe@example.test",
                     f"sub-dupe-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/dupe",
            dedupe_key="url:https://example.test/dupe",
            owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        with pytest.raises(IntegrityError) as caught:
            s.add(models.UserMemory(user_id=uid, content_id=asset.id))
            s.commit()
        assert "user_memories_user_content_uq" in str(caught.value), caught.value
        s.rollback()


def test_two_users_keep_separate_memories_of_one_shared_asset(sessions):
    """The uniqueness is per user, so sharing is still possible."""
    import app.models as models

    a = _make_user(sessions, "share-a@example.test",
                   f"sub-sa-{uuid.uuid4().hex[:8]}")
    b = _make_user(sessions, "share-b@example.test",
                   f"sub-sb-{uuid.uuid4().hex[:8]}")
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/shared-ok",
            dedupe_key="url:https://example.test/shared-ok",
            visibility="PUBLIC", owner_user_id=None)
        s.add(asset)
        s.flush()
        for uid in (a, b):
            s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.commit()
        assert s.query(models.UserMemory).filter(
            models.UserMemory.content_id == asset.id).count() == 2


def test_one_subject_cannot_belong_to_two_users(sessions):
    """UNIQUE on auth_subject, enforced by the database."""
    subject = f"sub-unique-{uuid.uuid4().hex[:8]}"
    _make_user(sessions, "subj-a@example.test", subject)
    with pytest.raises(IntegrityError) as caught:
        _make_user(sessions, "subj-b@example.test", subject)
    assert "users_auth_subject_uq" in str(caught.value), caught.value


def test_unbound_legacy_users_do_not_collide_with_each_other(sessions):
    """auth_subject is nullable, and PostgreSQL treats NULLs as distinct, so
    accounts that predate the column never block one another."""
    _make_user(sessions, "legacy-a@example.test", None)
    _make_user(sessions, "legacy-b@example.test", None)
    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM users WHERE auth_subject IS NULL")).scalar() >= 2


def test_a_private_asset_is_owned_by_exactly_one_user(sessions):
    """owner_user_id is a real foreign key: an asset cannot be owned by a user
    who does not exist, which is what makes 'private' mean anything."""
    with sessions() as s:
        s.add(_content_asset_model()(
            canonical_url="https://example.test/orphan",
            dedupe_key="url:https://example.test/orphan",
            visibility="PRIVATE", owner_user_id=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def _content_asset_model():
    from app.models import ContentAsset
    return ContentAsset


def test_deleting_a_user_removes_only_their_own_rows(sessions):
    """ON DELETE CASCADE on user_id, checked against the composite FK.

    Postgres has to satisfy both constraints in one statement: the item cascade
    from `users`, and the memory cascade from `users` as well. Either one
    leaving a row behind would make the composite FK refuse the delete, so this
    also proves the two cascades are consistent.
    """
    a = _make_user(sessions, "cascade-a@example.test",
                   f"sub-ca-{uuid.uuid4().hex[:8]}")
    b = _make_user(sessions, "cascade-b@example.test",
                   f"sub-cb-{uuid.uuid4().hex[:8]}")
    a_item, _a_asset = _ready_item(sessions, a,
                                   "https://example.test/cascade-a")
    b_item, _b_asset = _ready_item(sessions, b,
                                   "https://example.test/cascade-b")

    with sessions() as s:
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": a})
        s.commit()
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": a}).scalar() == 0, "A's item survived"
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": a}).scalar() == 0, "A's memory survived"
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE id = :i"),
            {"i": b_item}).scalar() == 1, "deleting A removed B's item"
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": b}).scalar() == 1, "deleting A removed B's memory"


# --- 6b. deleting a save through the route ---------------------------------

def test_deleting_an_item_by_id_also_removes_the_users_memory(
        client, sessions, two_users):
    """The route must delete the whole save, not just the item row.

    The memory holds the note, the intent and the save history. Leaving it
    behind means a later save of the same link brings the note back, and the
    user's "delete" did not delete what they thought it did.
    """
    client.as_user(two_users["a"])
    assert client.request(
        "DELETE", f"/api/v1/items/{two_users['a_item']}").status_code == 204

    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": two_users["a"]}).scalar() == 0, "A's memory survived the delete"


def test_deleting_a_shared_save_leaves_the_other_users_memory(
        client, sessions, two_users):
    """One content, two users: deleting one save must not touch the other's."""
    import app.models as models

    a, b = two_users["a"], two_users["b"]
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://example.test/shared-save",
            dedupe_key="url:https://example.test/shared-save",
            visibility="PUBLIC", owner_user_id=None,
            processing_status="READY", title="Shared save")
        s.add(asset)
        s.flush()
        asset_id = str(asset.id)
        for uid in (a, b):
            s.add(models.UserMemory(user_id=uid, content_id=asset.id))
            s.flush()
            s.add(models.Item(
                user_id=uid, url=f"https://example.test/shared-save/{uid}",
                canonical_url=f"https://example.test/shared-save/{uid}",
                content_id=asset.id, title="Shared save", status="ready"))
            s.flush()
        s.commit()
        a_item = str(s.execute(text(
            "SELECT id FROM items WHERE user_id = :u AND content_id = :c"),
            {"u": a, "c": asset_id}).scalar())

    client.as_user(a)
    assert client.request(
        "DELETE", f"/api/v1/items/{a_item}").status_code == 204

    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM user_memories "
            "WHERE user_id = :u AND content_id = :c"),
            {"u": a, "c": asset_id}).scalar() == 0, "A's memory survived"
        assert s.execute(text(
            "SELECT count(*) FROM user_memories "
            "WHERE user_id = :u AND content_id = :c"),
            {"u": b, "c": asset_id}).scalar() == 1, (
            "B's memory for the SAME content was deleted")
        assert s.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u AND content_id = :c"),
            {"u": b, "c": asset_id}).scalar() == 1, "B's item was deleted"

