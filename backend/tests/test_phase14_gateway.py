"""Phase 14: AI provider abstraction and privacy.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase14_gateway.py -q

Two claims: the current provider can be swapped for a fake without touching any
calling code, and no raw private content reaches a normal log.

The retention tests at the end need a database and skip without one.
"""
import asyncio
import logging
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 14 retention tests",
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
    name = f"fb_phase14_{uuid.uuid4().hex[:10]}"
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
    """Two users sharing one PUBLIC asset: the leak case for user context."""
    import app.models as models

    a, b = uuid.uuid4(), uuid.uuid4()
    with sessions() as s:
        for uid in (a, b):
            s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                      {"i": uid, "e": f"{uid.hex[:8]}@example.test"})
        asset = models.ContentAsset(
            canonical_url="https://example.test/shared-talk",
            dedupe_key="url:https://example.test/shared-talk",
            owner_user_id=a, visibility="PUBLIC", title="A talk")
        s.add(asset)
        s.flush()
        asset_id = asset.id
        for uid in (a, b):
            s.add(models.UserMemory(user_id=uid, content_id=asset_id))
        s.commit()
    return a, b, asset_id, {}

from app.schemas import Brief
from app.services import ai_gateway, privacy

# A saved page the user would not want to see in a log file.
PRIVATE_PAGE = (
    "Meeting notes from the acquisition review with Northwind Holdings. "
    "The purchase price discussed was forty two million dollars and the "
    "due diligence findings are attached below for review by the team."
)


# A short piece of private content: below PrivacyFilter.MIN_RUN_CHARS, so only
# the registry of registered values can catch it, never the long-run heuristic.
PRIVATE_NOTE = "Northwind Holdings, forty two million dollars."


class FakeProvider:
    """A complete stand-in for a provider: no network, no keys, no HTTP."""

    name = "fake"

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else {
            "title": "Northwind notes",
            "overview": "Notes from the acquisition review.",
            "highlights": ["Price discussed: 42m"],
            "structured_data": {"content_type": "article"},
        }
        self.seen_prompts = []
        self.seen_texts = []

    async def generate_json(self, system_prompt, user_prompt, *,
                            temperature=0.0):
        self.seen_prompts.append((system_prompt, user_prompt))
        return dict(self.answer)

    async def embed(self, texts, *, task="document"):
        self.seen_texts.extend(texts)
        return [[float(len(t) % 7)] * 1536 for t in texts]

    async def embed_one(self, text, *, task="document"):
        self.seen_texts.append(text)
        return [float(len(text) % 7)] * 1536

    def model_name(self):
        return "fake-model-v1"


@pytest.fixture
def fake_gateway():
    """Install a fake provider and put the real one back afterwards."""
    fake = FakeProvider()
    previous = ai_gateway.set_gateway(ai_gateway.Gateway(adapter=fake))
    yield fake
    ai_gateway.set_gateway(previous)
    privacy.reset_private()


@pytest.fixture
def clean_privacy():
    """No registered content, so each privacy test starts from a known state."""
    privacy.reset_private()
    yield
    privacy.reset_private()


# --- the gateway can be swapped --------------------------------------------

def test_the_default_provider_is_still_the_current_one():
    """Phase 14 must not switch providers. This pins that."""
    gateway = ai_gateway.get_gateway()
    assert isinstance(gateway.adapter, ai_gateway.ProviderAdapter)


def test_structured_generation_goes_through_the_fake(fake_gateway):
    import asyncio

    from app.services import extractor

    brief = asyncio.run(extractor.extract_brief(PRIVATE_PAGE, "Northwind"))
    assert isinstance(brief, Brief)
    assert brief.title == "Northwind notes"
    assert fake_gateway.seen_prompts, "the fake never received the prompt"


def test_embeddings_go_through_the_fake(fake_gateway):
    import asyncio

    from app.services import embedder

    vectors = asyncio.run(embedder.embed_many(["a", "b"]))
    assert len(vectors) == 2
    assert fake_gateway.seen_texts == ["a", "b"]

    one = asyncio.run(embedder.embed_text("c"))
    assert len(one) == 1536
    assert embedder.embedding_model_name() == "fake-model-v1"


def test_a_different_provider_needs_no_caller_change(fake_gateway):
    """Swap in a second fake: the extractor is untouched and still works."""
    import asyncio

    from app.services import extractor

    other = FakeProvider(answer={
        "title": "Other provider", "overview": "From somewhere else.",
        "highlights": ["h"], "structured_data": {"content_type": "article"},
    })
    previous = ai_gateway.set_gateway(ai_gateway.Gateway(adapter=other))
    try:
        brief = asyncio.run(extractor.extract_brief(PRIVATE_PAGE, "Northwind"))
        assert brief.title == "Other provider"
        assert other.seen_prompts, "the second provider was never called"
    finally:
        ai_gateway.set_gateway(previous)


def test_a_provider_can_be_registered_and_selected_by_name():
    """Swapping through configuration is a registry entry, not a code change."""
    ai_gateway.register_provider("fake", FakeProvider)
    gateway = ai_gateway.provider_from_config("fake")
    assert gateway.adapter.name == "fake"
    # An unknown name must fall back rather than take the service down.
    fallback = ai_gateway.provider_from_config("does-not-exist")
    assert isinstance(fallback.adapter, ai_gateway.ProviderAdapter)


def test_the_gateway_satisfies_every_interface(fake_gateway):
    """The gateway itself, not the adapter, is what callers depend on."""
    gateway = ai_gateway.get_gateway()
    for interface in (ai_gateway.StructuredGenerator, ai_gateway.Embedder):
        assert isinstance(gateway, interface), interface.__name__


def test_the_extractor_interface_is_satisfied_by_the_gateway(fake_gateway):
    """`extract_brief` is the extraction capability, on the gateway itself."""
    gateway = ai_gateway.get_gateway()
    assert gateway.adapter is fake_gateway
    brief = asyncio.run(gateway.extract_brief(PRIVATE_PAGE, "Northwind"))
    assert isinstance(brief, Brief)
    assert fake_gateway.seen_prompts, "extraction did not reach the fake"


def test_no_module_outside_the_adapter_imports_a_provider_sdk():
    """Provider details live in one module. This is the rule that keeps it so."""
    import pathlib

    services = pathlib.Path(ai_gateway.__file__).parent
    offenders = []
    for path in services.glob("*.py"):
        if path.name == "ai.py":  # the adapter itself
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import openai", "import anthropic",
                                "import google", "import cohere",
                                "from openai", "from anthropic", "from google")):
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], f"provider SDKs imported outside the adapter: {offenders}"


def test_only_the_adapter_calls_the_provider_transport():
    """Nothing above the gateway may call the transport module directly."""
    import pathlib

    services = pathlib.Path(ai_gateway.__file__).parent
    callers = set()
    for path in services.glob("*.py"):
        if path.name == "ai.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "ai.chat_json" in line or "ai.embed_text" in line:
                callers.add(path.name)
    assert callers <= {"ai_gateway.py"}, f"provider called directly from {callers}"


# --- private content does not reach normal logs ----------------------------

@pytest.fixture
def captured(clean_privacy):
    """A logger whose records are captured after the privacy filter runs."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(privacy.PrivacyFilter())
    logger = logging.getLogger("findback.test-private")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield logger, stream
    logger.removeHandler(handler)


def test_a_saved_page_never_appears_in_a_log(captured, fake_gateway):
    """The headline claim: the content sent to the provider never reaches a log.

    A provider error quotes the request it failed, so the prompt -- which holds
    the page -- is exactly what ends up in a log line. The page is short on
    purpose: the old filter only caught runs of 200+ characters, and it searched
    for the value digest, so this leaked the whole thing.
    """
    import asyncio

    from app.services import extractor

    logger, stream = captured
    asyncio.run(extractor.extract_brief(PRIVATE_NOTE, "Northwind"))
    assert fake_gateway.seen_prompts, "the fake never received the prompt"
    sent = fake_gateway.seen_prompts[0][1]
    assert PRIVATE_NOTE in sent, "precondition: the page reached the provider"

    # What a transport error logs: the request it could not send.
    logger.warning("provider rejected the request: %s", sent)

    output = stream.getvalue()
    assert PRIVATE_NOTE not in output
    assert "[private" in output


def test_the_filter_redacts_a_long_body_of_content(captured):
    logger, stream = captured
    logger.warning("provider rejected the request containing: %s", PRIVATE_PAGE)
    output = stream.getvalue()
    assert PRIVATE_PAGE not in output
    assert "[private" in output, output


def test_a_short_ordinary_message_survives(captured):
    """The filter must not destroy normal operational logging."""
    logger, stream = captured
    logger.info("processed item abc123 in 42ms")
    assert "processed item abc123 in 42ms" in stream.getvalue()


def test_an_exception_message_is_redacted_too(captured):
    logger, stream = captured
    try:
        raise RuntimeError(f"failed while saving {PRIVATE_PAGE}")
    except RuntimeError:
        logger.exception("ingest failed")
    assert PRIVATE_PAGE not in stream.getvalue()


def test_the_extractor_describes_a_bad_model_answer_without_printing_it(captured,
                                                                       fake_gateway):
    """The leak that already existed: the model answer is derived private data."""
    import asyncio

    from app.services import extractor

    logger, stream = captured
    fake_gateway.answer = {"nonsense": PRIVATE_PAGE}
    logger.addFilter(privacy.PrivacyFilter())

    brief = asyncio.run(extractor.extract_brief(PRIVATE_PAGE, "Northwind"))
    assert isinstance(brief, Brief), "a bad answer falls back, it does not crash"
    assert PRIVATE_PAGE not in stream.getvalue()


def test_describe_never_reveals_the_value():
    described = privacy.describe(PRIVATE_PAGE, "answer")
    assert PRIVATE_PAGE not in described
    assert str(len(PRIVATE_PAGE)) in described
    assert "Northwind" not in described


def test_registering_private_content_keeps_only_a_digest():
    """The filter must not become a second copy of the private data."""
    privacy.reset_private()
    privacy.register_private(PRIVATE_PAGE)
    stored = privacy.private_snapshot()
    assert PRIVATE_PAGE not in stored
    assert len(stored) == 1
    assert all(len(token) == 12 for token in stored)
    privacy.reset_private()


def test_the_filter_keeps_a_record_it_cannot_format(captured):
    """A malformed record must not be dropped: losing it hides a real bug."""
    logger, _stream = captured
    record = logging.LogRecord("x", logging.INFO, __file__, 1,
                               "%d %s", ("not-an-int",), None)
    assert privacy.PrivacyFilter().filter(record) is True


# --- retention is explicit -------------------------------------------------

def test_the_retention_policy_is_readable_without_reading_code():
    from app.services import retention

    policy = retention.retention_policy()
    assert "raw_text_hours" in policy
    assert "derived_text" in policy
    assert "asset" in policy


@needs_db
def test_deletion_removes_a_user_s_data_but_not_the_shared_asset(db, sessions):
    """Delete one user's save. The asset survives -- it may be public."""
    import app.models as models
    from app.services import retention

    a, b, asset_id, _memories = _two_users_one_asset(sessions)

    for uid in (a, b):
        with sessions() as s:
            s.add(models.Item(user_id=uid, url="https://example.test/shared-talk",
                              canonical_url="https://example.test/shared-talk",
                              title="A talk", content_id=asset_id,
                              raw_text="the fetched page", status="ready"))
            s.commit()

    with sessions() as s:
        counts = retention.delete_save(s, a, asset_id)
    assert counts["items"] == 1, counts
    assert counts["user_memories"] == 1, counts

    with db.connect() as conn:
        asset_alive = conn.execute(text(
            "SELECT count(*) FROM content_assets WHERE id = :i"),
            {"i": asset_id}).scalar()
        b_alive = conn.execute(text(
            "SELECT count(*) FROM items WHERE user_id = :u"),
            {"u": b}).scalar()
    assert asset_alive == 1, "deleting one user's save removed the shared asset"
    assert b_alive == 1, "deleting one user's save removed another's data"


@needs_db
def test_raw_text_is_dropped_but_the_derived_data_survives(db, sessions):
    """Retention removes the copy of the page, not the memory itself."""
    import app.models as models
    from app.services import retention

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": "keeper@example.test"})
        asset = models.ContentAsset(canonical_url="https://example.test/keep",
                                    dedupe_key="url:https://example.test/keep",
                                    owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        # Phase 16: items_user_content_fk requires this row to exist before an
        # item may name the asset. Real saves always create it, so the fixture
        # creates it too. Setup only.
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        item = models.Item(user_id=uid, url="https://example.test/keep",
                           canonical_url="https://example.test/keep",
                           content_id=asset.id, status="ready",
                           raw_text="a whole fetched page of the user's content",
                           normalized_text="a whole fetched page",
                           summary="The brief", key_points=["a"],
                           search_text="The brief a")
        s.add(item)
        s.flush()
        item_id = item.id
        s.commit()

    with sessions() as s:
        assert retention.purge_raw_text(s) == 1

    with db.connect() as conn:
        row = conn.execute(text(
            "SELECT raw_text, normalized_text, summary, search_text "
            "FROM items WHERE id = :i"), {"i": item_id}).mappings().one()
    assert row["raw_text"] is None
    assert row["normalized_text"] is None
    assert row["summary"] == "The brief", "the brief must survive retention"
    assert row["search_text"] is not None, "the memory must stay findable"


@needs_db
def test_delete_save_reports_the_chunks_it_removed(db, sessions):
    """The per-table count has to be real, not a placeholder."""
    import app.models as models
    from app.services import retention

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": "chunkcounter@example.test"})
        asset = models.ContentAsset(
            canonical_url="https://example.test/chunks",
            dedupe_key="url:https://example.test/chunks",
            owner_user_id=uid, visibility="UNKNOWN")
        s.add(asset)
        s.flush()
        s.add(models.UserMemory(user_id=uid, content_id=asset.id))
        s.flush()
        item = models.Item(user_id=uid, url="https://example.test/chunks",
                           canonical_url="https://example.test/chunks",
                           content_id=asset.id, status="ready")
        s.add(item)
        s.flush()
        for index in range(2):
            s.add(models.Chunk(item_id=item.id, chunk_idx=index,
                               chunk_text=f"chunk {index}",
                               embedding=[0.0] * 1536))
        s.flush()
        item_id = str(item.id)
        asset_id = str(asset.id)
        s.commit()

    with sessions() as s:
        counts = retention.delete_save(s, uid, asset_id, item_ids=[item_id])

    assert counts["items"] == 1, counts
    assert counts["chunks"] == 2, counts

