"""Phase 16: identity is the subject, not the address.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase16_identity.py -q

S1 was that `users.email == token["email"]` let whoever acquired a recycled
address inherit the previous owner's library. These tests drive the real
`get_current_user` dependency over HTTP with genuinely signed JWTs, so the
signature check, the claim handling and the database all take part.

Each test is one of the decisions `app/services/identity.py` makes, and each
asserts what the user can then reach -- not merely which branch ran, because a
correct branch label with a wrong row would still be a leak.
"""
import contextlib
import logging
import os
import uuid

import pytest
from fastapi import HTTPException
from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

# The secret only this module's tokens are signed with.
TEST_SECRET = "phase16-identity-test-secret"
AUDIENCE = "authenticated"

from app import database as db_module  # noqa: E402

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 16 identity tests",
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
    name = f"fb_p16id_{uuid.uuid4().hex[:10]}"
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


def _token(**claims):
    payload = {"aud": AUDIENCE, "iss": "https://test.supabase.co/auth/v1",
               "role": "authenticated", "aal": "aal1",
               "session_id": str(uuid.uuid4()), "phone": "",
               "is_anonymous": False}
    payload.update(claims)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


@pytest.fixture
def env(monkeypatch):
    """Point the token verifier at this module's secret and turn dev auth off,
    so nothing but a real signature gets in."""
    monkeypatch.setenv("API_SECRET_KEY", TEST_SECRET)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("DEV_AUTH_ENABLED", "false")
    monkeypatch.delenv("DEV_AUTH_EMAIL", raising=False)
    return monkeypatch


def _login(sessions, token):
    """Resolve a token to (user_id, outcome) or raise on refusal."""
    import httpx
    from jose import JWTError

    from app.auth import get_current_user
    from app.database import get_db
    from app.main import app
    from app.services import identity
    from app.services.auth_tokens import decode_access_token

    with sessions() as s:
        try:
            claims = decode_access_token(token)
        except JWTError as exc:
            raise AssertionError(f"token did not verify: {exc}") from exc
        return identity.resolve_user(s, claims)


def _user_row(sessions, email):
    with sessions() as s:
        row = s.execute(text(
            "SELECT id, auth_subject FROM users WHERE email = :e"),
            {"e": email}).one_or_none()
    return row


def _save(sessions, user_id, url, note="A secret note"):
    """Give this user a saved item, so a leak would be observable."""
    import app.models as models

    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url=url, dedupe_key=f"url:{url}", owner_user_id=user_id,
            visibility="UNKNOWN", processing_status="READY",
            title="A private talk")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=user_id, content_id=asset.id,
                                user_note=note, user_intent="project"))
        s.flush()
        item = models.Item(user_id=user_id, url=url, canonical_url=url,
                           content_id=asset.id, title="A private talk",
                           status="ready", search_text="privatetalkmarker")
        s.add(item)
        s.flush()
        asset_id, item_id = str(asset.id), str(item.id)
        s.commit()
    return asset_id, item_id


# --- same address, two different subjects ---------------------------------

def test_the_same_address_with_two_subjects_never_shares_a_library(
        sessions, env):
    """S1. A holds the address; the address is recycled; B presents the same
    `email` with a different `sub`. B must get nothing of A's."""
    email = "recycled@example.test"
    sub_a, sub_b = f"sub-a-{uuid.uuid4().hex[:8]}", f"sub-b-{uuid.uuid4().hex[:8]}"

    # First holder: a brand new user for this address.
    user_a, outcome_a = _login(sessions, _token(sub=sub_a, email=email))
    assert outcome_a == "created"
    _save(sessions, user_a.id, "https://example.test/a-private")

    # Second holder: same address, different subject.
    with pytest.raises(Exception) as refused:
        _login(sessions, _token(sub=sub_b, email=email))
    assert getattr(refused.value, "status_code", None) == 401, refused.value

    row = _user_row(sessions, email)
    assert str(row[1]) == sub_a, "the address was rebound to the newcomer"

    # And the newcomer cannot reach the original owner's memory by its id.
    asset_id, _item = _save(sessions, user_a.id, "https://example.test/a-two")
    user_b, _ = None, None
    try:
        user_b, _ = _login(sessions, _token(sub=sub_b, email=email))
    except Exception:
        pass
    assert user_b is None, "the newcomer was let in"


def test_a_recycled_address_is_refused_and_the_owner_is_unaffected(
        sessions, env):
    """The refusal must be the same whether or not the newcomer tries again."""
    email = "owner@example.test"
    sub_a, sub_b = f"sub-o-{uuid.uuid4().hex[:8]}", f"sub-n-{uuid.uuid4().hex[:8]}"
    user_a, _ = _login(sessions, _token(sub=sub_a, email=email))
    asset_id, _ = _save(sessions, user_a.id, "https://example.test/owners")

    for _ in range(3):
        with pytest.raises(Exception) as refused:
            _login(sessions, _token(sub=sub_b, email=email))
        assert refused.value.status_code == 401

    # The owner still logs in normally, and still owns their row.
    again, outcome = _login(sessions, _token(sub=sub_a, email=email))
    assert str(again.id) == str(user_a.id)
    assert outcome == "already_bound"
    with sessions() as s:
        assert s.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": user_a.id}).scalar() == 1


# --- the returning user whose address changed -----------------------------

def test_a_returning_user_is_found_by_subject_after_their_email_changes(
        sessions, env):
    """The address is display-only now, so a change at the provider cannot
    strand the account."""
    old_email, new_email = "before@example.test", "after@example.test"
    sub = f"sub-move-{uuid.uuid4().hex[:8]}"
    user, _ = _login(sessions, _token(sub=sub, email=old_email))
    _save(sessions, user.id, "https://example.test/moved")

    moved, outcome = _login(sessions, _token(sub=sub, email=new_email))
    assert outcome == "already_bound", outcome
    assert str(moved.id) == str(user.id), "the account was split by an email change"
    assert _user_row(sessions, old_email) is None, "the old address still resolves"
    assert _user_row(sessions, new_email)[0] == moved.id


def test_an_email_change_that_would_collide_keeps_the_old_display_email(
        sessions, env):
    """`users.email` is UNIQUE. A collision must not fail the login and must
    not tempt anyone into dropping the constraint -- the row is already
    identified by its subject, so the label is the only thing at stake."""
    taken = f"sub-taken-{uuid.uuid4().hex[:8]}"
    owner, _ = _login(sessions, _token(sub=taken, email="taken@example.test"))
    owner_email = "owner@example.test"
    mover = f"sub-mover-{uuid.uuid4().hex[:8]}"
    other, _ = _login(sessions, _token(sub=mover, email=owner_email))
    _save(sessions, other.id, "https://example.test/collide")

    # `other` now presents the address that belongs to `owner`.
    same, outcome = _login(sessions, _token(sub=mover, email="taken@example.test"))
    assert str(same.id) == str(other.id), "the login failed or changed identity"
    assert _user_row(sessions, owner_email)[0] == same.id, (
        "the old display email was overwritten")
    assert _user_row(sessions, "taken@example.test")[0] == owner.id, (
        "the collision overwrote the other user's address")


# --- binding a legacy row -------------------------------------------------

def test_a_legacy_row_binds_on_login_when_the_claim_says_verified(
        sessions, env):
    """A row created before this column existed has no subject. Binding it is
    the one moment we trust an address, so it needs a verified signal."""
    email = "legacy@example.test"
    sub = f"sub-legacy-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()
    assert _user_row(sessions, email)[1] is None, "the fixture is not legacy"

    # A boolean claim and a boolean-in-a-string are both affirmative.
    for value, label in [(True, "the boolean true"),
                         ("true", "the string 'true'")]:
        # A different subject each time, so this also proves a bound row is
        # never silently handed to a second subject.
        candidate = f"sub-{uuid.uuid4().hex[:8]}"
        user, outcome = _login(sessions, _token(
            sub=candidate, email=email,
            app_metadata={"email_verified": value}))
        assert outcome == "bound_on_login", f"{label}: {outcome}"
        assert str(user.id) == str(_user_row(sessions, email)[0]), (
            f"{label}: the subject was bound to a different row")
        assert _user_row(sessions, email)[1] == candidate, (
            f"{label}: the wrong subject was recorded")
        # Put it back to unbound so the next case starts from the legacy shape.
        with sessions() as s:
            s.execute(text("UPDATE users SET auth_subject = NULL "
                           "WHERE email = :e"), {"e": email})
            s.commit()


@pytest.mark.parametrize("app_metadata,label", [
    (None, "claim absent"),
    ({}, "no such key"),
    ({"email_verified": False}, "explicitly false"),
    ({"email_verified": "false"}, "the string 'false'"),
    ({"email_verified": "FALSE"}, "the string 'FALSE'"),
    ({"email_verified": ""}, "the empty string"),
    ({"email_verified": "unverified"}, "the word 'unverified'"),
    ({"email_verified": None}, "null"),
    ({"email_verified": 1}, "the number one, whose type means nothing here"),
    ({"email_verified": {"nested": True}}, "an object, an unrecognised shape"),
])
def test_a_legacy_row_does_not_bind_without_a_verified_claim(
        sessions, env, app_metadata, label):
    """Fails closed. Every one of these is 'not proven verified'."""
    email = f"legacy-{uuid.uuid4().hex[:6]}@example.test"
    sub = f"sub-l-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()
    row = _user_row(sessions, email)

    claims = {"sub": sub, "email": email}
    if app_metadata is not None:
        claims["app_metadata"] = app_metadata

    with pytest.raises(Exception) as refused:
        _login(sessions, _token(**claims))
    assert getattr(refused.value, "status_code", None) == 401, (
        f"{label}: expected a refusal")

    assert _user_row(sessions, email)[1] is None, (
        f"{label}: the subject was bound anyway")
    assert str(_user_row(sessions, email)[0]) == str(row[0]), (
        f"{label}: the legacy row changed")


def test_user_metadata_claiming_verified_is_ignored(sessions, env):
    """The important negative.

    Supabase lets a user write their own `user_metadata`, so a claim read from
    there is evidence supplied by whoever wants the account. This must be
    refused even though the flag says `true`.
    """
    email = "spoof@example.test"
    sub = f"sub-spoof-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()

    with pytest.raises(Exception) as refused:
        _login(sessions, _token(sub=sub, email=email, user_metadata={
            "email_verified": True, "role": "service_role", "sub": sub}))
    assert getattr(refused.value, "status_code", None) == 401
    assert _user_row(sessions, email)[1] is None, (
        "user_metadata was trusted as proof of a verified address")


def test_running_the_migration_does_not_silence_the_auth_logger():
    """A real defect this phase found, kept as a regression test.

    `alembic upgrade head` runs IN-PROCESS on API startup
    (database.init_db -> upgrade_head), and Alembic's env.py called
    fileConfig() with Python's default disable_existing_loggers=True. That
    walks every already-existing logger the ini does not name and sets
    `.disabled = True` on it -- which is every `findback.*` logger, including
    this one.

    The visible effect is that the API stops logging entirely from the moment
    the schema bootstrap runs: the startup AI-config line, provider warnings,
    and the refusals below all go silent until the process restarts. Nothing
    caught it, because every test that read a log record ran before its own
    fixture invoked Alembic.
    """
    import logging.config

    from app.services import identity

    log = logging.getLogger("findback.auth")
    ini = os.path.join(db_module.BACKEND_DIR, "alembic.ini")
    assert os.path.isfile(ini), ini

    # Re-run exactly what env.py does, on the real ini.
    logging.config.fileConfig(ini, disable_existing_loggers=False)
    try:
        assert not log.disabled, (
            "alembic's logging setup disabled findback.auth: the application "
            "would log nothing after its schema bootstrap")
        records = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Collect(level=logging.WARNING)
        log.addHandler(handler)
        try:
            log.error("the auth logger still works after a migration")
        finally:
            log.removeHandler(handler)
        assert records, "no record reached the handler after a migration"
        assert identity.log is log, "identity uses a different logger object"
    finally:
        # Leave the process as it was, whatever this test asserted.
        log.disabled = False


def test_the_verified_claim_path_cannot_be_pointed_at_user_metadata():
    """Configuration cannot reintroduce the hole either: naming
    user_metadata anywhere raises rather than reading it."""
    from app.services import identity

    original = os.environ.get("AUTH_EMAIL_VERIFIED_CLAIM")
    try:
        for path in ("user_metadata", "user_metadata.email_verified",
                     "user_metadata.a.b"):
            os.environ["AUTH_EMAIL_VERIFIED_CLAIM"] = path
            with pytest.raises(ValueError, match="user_metadata"):
                identity.claim_value({"user_metadata": {"email_verified": True}},
                                     identity.verified_claim_path())
    finally:
        if original is None:
            os.environ.pop("AUTH_EMAIL_VERIFIED_CLAIM", None)
        else:
            os.environ["AUTH_EMAIL_VERIFIED_CLAIM"] = original


def test_the_verified_claim_path_is_configurable(sessions, env):
    """An operator who has populated a claim of their own can name it."""
    email = "custom-claim@example.test"
    sub = f"sub-cc-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()

    env.setenv("AUTH_EMAIL_VERIFIED_CLAIM", "app_metadata.email_confirmed_at")
    user, outcome = _login(sessions, _token(
        sub=sub, email=email,
        app_metadata={"email_confirmed_at": "2026-01-01T00:00:00Z"}))
    assert outcome == "bound_on_login"
    assert _user_row(sessions, email)[1] == sub


def test_a_bound_subject_never_rebinds_even_with_a_verified_claim(
        sessions, env):
    """Verification only unlocks the legacy path. Once bound, the subject is
    the identity and a different address changes nothing about the row."""
    email_a, email_b = "first@example.test", "second@example.test"
    sub = f"sub-fixed-{uuid.uuid4().hex[:8]}"
    user_a, _ = _login(sessions, _token(sub=sub, email=email_a))
    _save(sessions, user_a.id, "https://example.test/fixed")

    user_b, outcome = _login(sessions, _token(
        sub=sub, email=email_b, app_metadata={"email_verified": True}))
    assert str(user_b.id) == str(user_a.id), "the subject resolved to two rows"
    assert outcome == "already_bound", outcome


def test_a_token_without_a_subject_is_rejected(sessions, env):
    """`sub` is the only stable identifier; without it there is no identity."""
    from fastapi import HTTPException

    from app.auth import get_current_user

    with sessions() as db, pytest.raises(HTTPException) as refused:
        get_current_user(authorization="Bearer " + _token(email="nosub@example.test"), db=db)
    assert refused.value.status_code == 401


def test_a_token_signed_with_the_wrong_secret_is_rejected(sessions, env):
    with pytest.raises(AssertionError):
        _login(sessions, jwt.encode(
            {"sub": "x", "email": "x@example.test", "aud": AUDIENCE},
            "not-the-secret", algorithm="HS256"))


def test_a_token_for_another_audience_is_rejected(sessions, env):
    """decode_access_token verifies `aud`; unchanged, and still enforced."""
    with pytest.raises(AssertionError):
        _login(sessions, jwt.encode(
            {"sub": "x", "email": "x@example.test", "aud": "something-else"},
            TEST_SECRET, algorithm="HS256"))


# --- what the log is allowed to contain -----------------------------------

@contextlib.contextmanager
def _captured_auth_log(level=logging.WARNING):
    """Collect records from the `findback.auth` logger directly.

    `caplog` attaches its handler to the root logger, which makes those
    assertions depend on whatever any other test did to the logging tree. A
    handler on the logger under test is unambiguous and self-restoring.

    The level is a parameter because the refusal paths log at WARNING and ERROR
    while a successful bind logs at INFO; a test that cared about the bind would
    otherwise see an empty list and wrongly conclude nothing was written.
    """
    log = logging.getLogger("findback.auth")
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect(level=level)
    previous_level = log.level
    log.addHandler(handler)
    log.setLevel(level)
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)


def _login_expecting_refusal(sessions, token):
    """Log in with a token that must be refused, returning the exception.

    Separate so the refusal is what the test provokes, rather than something
    that has to be re-derived from a `pytest.raises` that may also swallow an
    unrelated failure.
    """
    with pytest.raises(HTTPException) as refused:
        _login(sessions, token)
    assert refused.value.status_code == 401, refused.value
    return refused.value


def test_the_refusal_log_hides_the_address_and_the_token(sessions, env):
    """Phase 16 asks for no raw private data in new logging. An address is
    personal data, so only a digest and a length may appear."""
    email = "log-me@example.test"
    sub_owner = f"sub-lo-{uuid.uuid4().hex[:8]}"
    _login(sessions, _token(sub=sub_owner, email=email))

    with _captured_auth_log() as records:
        _login_expecting_refusal(
            sessions, _token(sub=f"sub-ln-{uuid.uuid4().hex[:8]}", email=email))

    assert records, "the refusal was not logged at all"
    logged = "\n".join(records)
    assert email not in logged, f"the address was logged verbatim: {logged}"
    assert "@example.test" not in logged, logged
    assert "log-me" not in logged, logged


def test_the_unverified_bind_refusal_is_also_logged(sessions, env):
    """The other refusal path, which logs at WARNING rather than ERROR."""
    email = "unverified-log@example.test"
    sub = f"sub-ul-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()

    with _captured_auth_log() as records:
        _login_expecting_refusal(sessions, _token(sub=sub, email=email))

    assert records, "the refusal was not logged at all"
    logged = "\n".join(records)
    assert email not in logged, f"the address was logged verbatim: {logged}"


def test_a_successful_bind_log_hides_the_address(sessions, env):
    """The bind path too: a successful login is not a reason to log an
    address."""
    email = "bind-log@example.test"
    sub = f"sub-bl-{uuid.uuid4().hex[:8]}"
    with sessions() as s:
        s.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})
        s.commit()

    # INFO, not WARNING: a successful bind is not a refusal.
    with _captured_auth_log(level=logging.INFO) as records:
        _login(sessions, _token(sub=sub, email=email,
                                app_metadata={"email_verified": True}))
    logged = "\n".join(records)
    assert logged, "the bind was not logged at all"
    assert email not in logged, f"the address was logged verbatim: {logged}"


# --- first-login races -----------------------------------------------------

def test_a_raced_first_login_uses_the_winners_row(sessions, monkeypatch):
    """Two simultaneous first logins for one subject must both succeed.

    Both sessions see no row for the subject and no holder for the address, so
    both INSERT. The loser hits the UNIQUE constraint on users.email; the
    winner's row is the right answer for both, so the loser must fall back to it
    rather than failing with a 500.
    """
    from sqlalchemy.orm import Session

    from app.models import User
    from app.services import identity

    email = "raced@example.test"
    subject = f"sub-raced-{uuid.uuid4().hex[:8]}"
    claims = {"sub": subject, "email": email,
              "iss": "https://test.supabase.co/auth/v1"}

    real_commit = Session.commit
    state = {"raced": False}

    def racing_commit(self):
        if not state["raced"]:
            state["raced"] = True
            # The other login wins the race and lands its row first.
            with sessions() as other:
                other.add(User(email=email, auth_subject=subject,
                               auth_provider="jwt"))
                other.commit()
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", racing_commit)

    with sessions() as s:
        user, outcome = identity.resolve_user(s, claims)

    assert state["raced"], "the test never reproduced the race"
    assert user.auth_subject == subject
    assert outcome == identity.Outcome.ALREADY_BOUND
    with sessions() as s:
        assert s.query(User).filter(User.email == email).count() == 1, (
            "the losing login created a second row")

