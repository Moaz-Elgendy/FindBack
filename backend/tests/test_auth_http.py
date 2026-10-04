"""Phase 16: the real auth dependency over HTTP, with real signed tokens.

    TEST_DATABASE_URL=... python -m pytest tests/test_auth_http.py -q

Every other Phase 16 test either calls `identity.resolve_user` directly or
overrides `get_current_user`. Neither exercises what actually runs in
production:

    Authorization: Bearer <jwt> -> decode_access_token -> resolve_user -> route

These tests send the header, let the real dependency verify the signature and
resolve the account, and then assert what the caller can reach. A broken
dependency, a bypassed signature check or a route that forgets its user filter
all show up here and nowhere else.
"""
import asyncio
import os
import uuid

import pytest
from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import database as db_module

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

# The secret only this module's tokens are signed with.
TEST_SECRET = "auth-http-test-secret"
AUDIENCE = "authenticated"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping auth HTTP tests",
)

import httpx  # noqa: E402

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
    name = f"fb_authhttp_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
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
def sessions(db):
    return sessionmaker(bind=db)


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """Sign with this module's secret, and turn the dev identity off.

    With `DEV_AUTH_ENABLED` true a request with NO header would resolve to the
    dev user, and the 401 tests below would pass for the wrong reason.
    """
    monkeypatch.setenv("API_SECRET_KEY", TEST_SECRET)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("DEV_AUTH_ENABLED", "false")
    monkeypatch.delenv("DEV_AUTH_EMAIL", raising=False)


def _token(sub, email, secret=TEST_SECRET, **overrides):
    payload = {"sub": sub, "email": email, "aud": AUDIENCE,
               "iss": "https://test.supabase.co/auth/v1", "role": "authenticated"}
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def client(db, sessions):
    """The real app. Only the database is overridden, never the identity."""
    saved_engine = db_module._engine
    saved_sessionmaker = db_module._sessionmaker
    db_module._engine = db
    db_module._sessionmaker = sessions

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    class Harness:
        def request(self, method, path, token=None, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"

            async def _send():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport,
                                             base_url="http://test") as c:
                    return await c.request(method, path, headers=headers, **kwargs)
            return asyncio.run(_send())

    try:
        yield Harness()
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_module._engine = saved_engine
        db_module._sessionmaker = saved_sessionmaker


def _user(sessions, email, subject):
    with sessions() as s:
        s.execute(text("INSERT INTO users (email, auth_subject, auth_provider) "
                       "VALUES (:e, :s, 'test')"), {"e": email, "s": subject})
        s.commit()
        return s.execute(text("SELECT id FROM users WHERE email = :e"),
                         {"e": email}).scalar()


def _item(sessions, user_id, url):
    import app.models as models

    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url=url, dedupe_key=f"url:{url}", owner_user_id=user_id,
            visibility="UNKNOWN", processing_status="READY", title="A private talk")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=user_id, content_id=asset.id))
        s.flush()
        item = models.Item(
            user_id=user_id, url=url, canonical_url=url, content_id=asset.id,
            title="A private talk", title_clean="A private talk",
            summary="A private talk", status="ready")
        s.add(item)
        s.flush()
        item_id = str(item.id)
        s.commit()
        return item_id


def test_a_valid_token_reaches_the_users_own_item(client, sessions):
    """The whole path, with a signature that really verifies."""
    subject = f"sub-{uuid.uuid4().hex[:8]}"
    user_id = _user(sessions, "valid@example.test", subject)
    item_id = _item(sessions, user_id, "https://example.test/valid")

    response = client.request("GET", f"/api/v1/items/{item_id}",
                              token=_token(subject, "valid@example.test"))
    assert response.status_code == 200, response.text
    assert response.json()["id"] == item_id


def test_a_valid_token_creates_the_account_on_first_login(client, sessions):
    subject = f"sub-new-{uuid.uuid4().hex[:8]}"
    response = client.request("GET", "/api/v1/items",
                              token=_token(subject, "first@example.test"))
    assert response.status_code == 200, response.text
    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM users WHERE auth_subject = :s"),
            {"s": subject}).scalar() == 1


def test_a_missing_token_is_rejected(client):
    response = client.request("GET", "/api/v1/items")
    assert response.status_code == 401, response.text


def test_an_expired_token_is_rejected(client, sessions):
    subject = f"sub-{uuid.uuid4().hex[:8]}"
    _user(sessions, "expired@example.test", subject)
    response = client.request(
        "GET", "/api/v1/items",
        token=_token(subject, "expired@example.test", exp=1))
    assert response.status_code == 401, response.text


def test_a_wrong_signature_is_rejected(client, sessions):
    subject = f"sub-{uuid.uuid4().hex[:8]}"
    _user(sessions, "wrongsig@example.test", subject)
    response = client.request(
        "GET", "/api/v1/items",
        token=_token(subject, "wrongsig@example.test", secret="not-the-secret"))
    assert response.status_code == 401, response.text


def test_user_bs_token_never_returns_user_as_data(client, sessions):
    subject_a = f"sub-a-{uuid.uuid4().hex[:8]}"
    subject_b = f"sub-b-{uuid.uuid4().hex[:8]}"
    user_a = _user(sessions, "a-http@example.test", subject_a)
    user_b = _user(sessions, "b-http@example.test", subject_b)
    item_a = _item(sessions, user_a, "https://example.test/a-http")
    item_b = _item(sessions, user_b, "https://example.test/b-http")

    token_b = _token(subject_b, "b-http@example.test")

    listed = client.request("GET", "/api/v1/items", token=token_b)
    assert listed.status_code == 200, listed.text
    ids = {str(i["id"]) for i in listed.json()["items"]}
    assert item_b in ids, "B's own item is missing"
    assert item_a not in ids, "B's token listed A's item"

    direct = client.request("GET", f"/api/v1/items/{item_a}", token=token_b)
    assert direct.status_code == 404, direct.text
    assert item_a not in direct.text
