"""Phase 13: "Why I saved this".

    TEST_DATABASE_URL=... python -m pytest tests/test_phase13_user_context.py -q

Three claims under test:

    a note and an intent can be set, updated and withdrawn
    they belong to one user and to nobody else
    writing them never touches the shared ContentAsset
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 13 user-context tests",
)

# The four reasons the phase names, written the way a person writes them.
NOTES_AND_INTENTS = [
    ("For my project", "project"),
    ("Want to try later", "try_later"),
    ("Thinking about buying", "consider_buying"),
    ("Research this later", "research"),
]


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
    name = f"fb_phase13_{uuid.uuid4().hex[:10]}"
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


def _two_users_one_asset(sessions):
    """Two users who saved the SAME content -- the case privacy lives or dies.

    Phase 2 deduplicates identical content into one ContentAsset, so if user A's
    note leaked onto the asset, user B would read it. This fixture makes that
    failure possible rather than hypothetical.
    """
    import app.models as models

    a, b = uuid.uuid4(), uuid.uuid4()
    with sessions() as s:
        for uid in (a, b):
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        asset = models.ContentAsset(
            canonical_url="https://example.test/shared-talk",
            dedupe_key="url:https://example.test/shared-talk",
            owner_user_id=a, visibility="PUBLIC", title="A talk about deploys")
        s.add(asset)
        s.flush()
        asset_id = asset.id
        memories = {}
        for uid in (a, b):
            memory = models.UserMemory(user_id=uid, content_id=asset_id)
            s.add(memory)
            s.flush()
            memories[uid] = memory.id
        s.commit()
    return a, b, asset_id, memories


def _asset_row(db, asset_id):
    with db.connect() as conn:
        return conn.execute(text(
            "SELECT title, brief, entities, topics, intent, structured_data "
            "FROM content_assets WHERE id = :i"), {"i": asset_id}).mappings().one()


def _memory_row(db, memory_id):
    with db.connect() as conn:
        return conn.execute(text(
            "SELECT user_note, user_intent, save_count FROM user_memories "
            "WHERE id = :i"), {"i": memory_id}).mappings().one()


# --- the note and the intent ------------------------------------------------

@pytest.mark.parametrize("note,intent", NOTES_AND_INTENTS,
                         ids=[i for _, i in NOTES_AND_INTENTS])
def test_a_note_and_intent_can_be_set(db, sessions, note, intent):
    from app.services import user_context

    a, _b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        memory = user_context.set_context(s, a, asset_id, note=note,
                                          intent=intent)
        assert memory.user_note == note
        assert memory.user_intent == intent

    row = _memory_row(db, memories[a])
    assert row["user_note"] == note
    assert row["user_intent"] == intent


def test_a_note_can_be_updated(db, sessions):
    from app.services import user_context

    a, _b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="first thought")
        memory = user_context.set_context(s, a, asset_id, note="second thought")
        assert memory.user_note == "second thought"
    assert _memory_row(db, memories[a])["user_note"] == "second thought"


def test_updating_the_intent_does_not_wipe_the_note(db, sessions):
    """Only supplied fields are written -- a note is not recoverable if lost."""
    from app.services import user_context

    a, _b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="keep me",
                                 intent="project")
        user_context.set_context(s, a, asset_id, intent="research")

    row = _memory_row(db, memories[a])
    assert row["user_note"] == "keep me", "the note survived an intent update"
    assert row["user_intent"] == "research"


def test_a_note_can_be_cleared_without_losing_the_save(db, sessions):
    from app.services import user_context

    a, _b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="regrettable",
                                 intent="try_later")
        memory = user_context.clear_context(s, a, asset_id)
        assert memory.user_note is None
        assert memory.user_intent is None

    row = _memory_row(db, memories[a])
    assert row["user_note"] is None
    assert row["save_count"] == 1, "withdrawing a note must not unsave it"


def test_an_unknown_intent_is_refused(db, sessions):
    """Intent is a closed set: a reason we do not define is not stored."""
    import pytest

    from app.services import user_context

    a, _b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        with pytest.raises(ValueError):
            user_context.set_context(s, a, asset_id, intent="vibes")
    assert _memory_row(db, memories[a])["user_intent"] is None


# --- privacy ---------------------------------------------------------------

def test_a_note_is_never_visible_to_another_user(db, sessions):
    """Two users saved the same content; A's reason is not B's business."""
    from app.services import user_context

    a, b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="for my project",
                                 intent="project")

    with sessions() as s:
        other = user_context.get_memory(s, b, asset_id)
        assert other.user_note is None
        assert other.user_intent is None
    assert _memory_row(db, memories[b])["user_note"] is None


def test_two_users_on_one_asset_keep_separate_notes(db, sessions):
    from app.services import user_context

    a, b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="for my project",
                                 intent="project")
        user_context.set_context(s, b, asset_id, note="thinking about buying",
                                 intent="consider_buying")

    assert _memory_row(db, memories[a])["user_note"] == "for my project"
    assert _memory_row(db, memories[b])["user_note"] == "thinking about buying"


def test_one_user_cannot_write_to_another_users_memory(db, sessions):
    """B writing must land on B's row, never on A's.

    The scoping is enforced by the query, not by the caller: `set_context` looks
    the row up by (user_id, content_id), so there is no id for one user to
    point at another user's row.
    """
    from app.services import user_context

    a, b, asset_id, memories = _two_users_one_asset(sessions)
    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="mine")
        user_context.set_context(s, b, asset_id, note="mine too")

    # Both wrote the same words. Each row kept its own copy.
    assert _memory_row(db, memories[a])["user_note"] == "mine"
    assert _memory_row(db, memories[b])["user_note"] == "mine too"
    assert memories[a] != memories[b], "the two users share one memory row"


def test_a_user_with_no_memory_of_the_content_cannot_create_one(db, sessions):
    """Writing requires an existing memory; it must not conjure one."""
    from app.services import user_context

    a, _b, asset_id, _memories = _two_users_one_asset(sessions)
    stranger = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": stranger, "e": "stranger@example.test"})
        s.commit()

    with sessions() as s:
        assert user_context.get_memory(s, stranger, asset_id) is None
        assert user_context.set_context(s, stranger, asset_id, note="hi") is None

    with db.connect() as conn:
        count = conn.execute(text(
            "SELECT count(*) FROM user_memories WHERE user_id = :u"),
            {"u": stranger}).scalar()
    assert count == 0, "a note was written for content this user never saved"


# --- separation from content understanding ---------------------------------

def test_writing_a_note_does_not_touch_the_shared_asset(db, sessions):
    """The asset is shared. The reason for saving must never be written there."""
    from app.services import user_context

    a, b, asset_id, _memories = _two_users_one_asset(sessions)
    before = dict(_asset_row(db, asset_id))

    with sessions() as s:
        user_context.set_context(s, a, asset_id,
                                 note="SECRET-MARKER-NOTE",
                                 intent="project")
    after = dict(_asset_row(db, asset_id))

    assert after == before, f"the asset changed: {before} -> {after}"
    with db.connect() as conn:
        dump = str(conn.execute(text(
            "SELECT row_to_json(c) FROM content_assets c WHERE id = :i"),
            {"i": asset_id}).mappings().one())
    assert "SECRET-MARKER-NOTE" not in dump, "the note reached the shared asset"


def test_content_understanding_and_user_context_stay_separate(db, sessions):
    """Content says WHAT it is; the user says WHY. Neither overwrites the other."""
    import app.models as models
    from app.services import user_context

    a, _b, asset_id, _memories = _two_users_one_asset(sessions)
    with sessions() as s:
        asset = s.query(models.ContentAsset).filter(
            models.ContentAsset.id == asset_id).one()
        asset.brief = "A talk about zero-downtime deploys."
        asset.topics = ["deployment", "kubernetes"]
        s.commit()

    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="for my project",
                                 intent="project")

    asset_after = dict(_asset_row(db, asset_id))
    assert asset_after["brief"] == "A talk about zero-downtime deploys."
    assert asset_after["topics"] == ["deployment", "kubernetes"], \
        "content understanding was disturbed by a user note"


def test_a_user_note_never_overwrites_the_content_intent(db, sessions):
    """`items.intent` is content-derived; `user_memories.user_intent` is the user's.

    They are different facts and Phase 13 must not conflate them.
    """
    import app.models as models
    from app.services import user_context

    a, _b, asset_id, _memories = _two_users_one_asset(sessions)
    with sessions() as s:
        # Phase 16: items_user_content_fk requires the memory row to exist
        # before an item may name the asset. _two_users_one_asset above already
        # created it for user `a`. Setup only -- no assertion changed.
        item = models.Item(user_id=a, url="https://example.test/shared-talk",
                           canonical_url="https://example.test/shared-talk",
                           title="A talk about deploys", content_id=asset_id,
                           intent="learn")
        s.add(item)
        s.flush()
        item_id = item.id
        s.commit()

    with sessions() as s:
        user_context.set_context(s, a, asset_id, note="for my project",
                                 intent="project")

    with db.connect() as conn:
        row = conn.execute(text("SELECT intent FROM items WHERE id = :i"),
                           {"i": item_id}).mappings().one()
    assert row["intent"] == "learn", "the content-derived intent was overwritten"


# --- inference stays inside the schema -------------------------------------

@pytest.mark.parametrize("text,intent", NOTES_AND_INTENTS,
                         ids=[i for _, i in NOTES_AND_INTENTS])
def test_the_examples_infer_to_their_own_intent(text, intent):
    """The four reasons the phase names, inferred from the user's own words."""
    from app.services.user_context import infer_intent

    assert infer_intent(text) == intent


@pytest.mark.parametrize("text", [
    "", "   ", "someday", "reference material", "no idea why I saved this",
])
def test_unclear_wording_infers_nothing(text):
    """"Not stated" is an answer. A wrong guess about why is worse than none."""
    from app.services.user_context import infer_intent

    assert infer_intent(text) is None


def test_inference_can_only_produce_a_defined_intent():
    """Nothing may be inferred that the schema does not already define."""
    from app.schemas import UserIntent
    from app.services.user_context import infer_intent

    defined = {member.value for member in UserIntent}
    for text in [note for note, _ in NOTES_AND_INTENTS] + [
            "buy it for the project", "research later", "try later on the team"]:
        result = infer_intent(text)
        assert result is None or result.value in defined


def test_the_schema_rejects_an_undefined_intent():
    """The API surface is closed too, not just the service."""
    from pydantic import ValidationError

    from app.schemas import UserContextIn

    assert UserContextIn(note="x", intent="project").intent.value == "project"
    with pytest.raises(ValidationError):
        UserContextIn(note="x", intent="because it looked nice")


def test_the_schema_rejects_unknown_fields():
    from pydantic import ValidationError

    from app.schemas import UserContextIn

    with pytest.raises(ValidationError):
        UserContextIn(note="x", intent="project", share_with="everyone")


def test_a_note_may_be_written_without_an_intent():
    from app.schemas import UserContextIn

    payload = UserContextIn(note="the one about the pool config")
    assert payload.note and payload.intent is None


def test_a_user_intent_cannot_be_a_substring_of_the_schema():
    """No enum can smuggle in a value the schema does not define."""
    from pydantic import ValidationError

    from app.schemas import UserContextIn

    for bad in ("PROJECT", "Project", "project; drop table", ""):
        with pytest.raises(ValidationError):
            UserContextIn(intent=bad)