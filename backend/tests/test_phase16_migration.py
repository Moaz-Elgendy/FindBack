"""Phase 16: what migration 0010 does to a database that already has data.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase16_migration.py -q

The composite foreign key is the isolation guarantee of this phase, so the
migration that installs it has to be exercised against real rows, not just
compiled. Three properties are claimed and checked here:

  it applies to an EMPTY database, and to a NON-EMPTY one
  it does not grant access it should not (no cross-user memory is created)
  it applies on top of the whole 0001 -> 0010 chain, and downgrades

`tests/test_phase1_migration.py` covers the 0002 backfill; this file covers the
0010 constraint, whose failure mode is different: a constraint can be added to
a populated table only if every existing row already satisfies it.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 16 migration tests",
)

OWNED = "https://example.test/owned"
PUBLIC = "https://example.test/public"
LEGACY = "https://example.test/legacy"
FOREIGN = "https://example.test/someone-elses-private"


@pytest.fixture(scope="module")
def admin_engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


def _fresh_db(admin_engine, prefix):
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    return admin, name, url


def _drop(admin, name, engine=None):
    if engine is not None:
        engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _alembic(url):
    from app import database as db_module
    return db_module.alembic_config(), url


def _upgrade(url, target="head"):
    from alembic import command
    cfg, url = _alembic(url)
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, target)


def _seed_the_hostile_rows(url):
    """Four items, three of which the constraint would otherwise reject.

    Deliberately inserted with raw SQL and NO memory rows, which is exactly the
    state the constraint is not allowed to fail on.
    """
    eng = create_engine(url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text("""
            INSERT INTO users (email) VALUES ('owner@example.test'),
                                              ('other@example.test')
        """))
        conn.execute(text("""
            INSERT INTO content_assets (id, canonical_url, visibility,
                                         owner_user_id)
            SELECT gen_random_uuid(), :url, 'UNKNOWN', id
              FROM users WHERE email = 'owner@example.test'
        """), {"url": OWNED})
        conn.execute(text("""
            INSERT INTO content_assets (id, canonical_url, visibility,
                                         owner_user_id)
            VALUES (gen_random_uuid(), :url, 'PUBLIC', NULL)
        """), {"url": PUBLIC})
        conn.execute(text("""
            INSERT INTO content_assets (id, canonical_url, visibility,
                                         owner_user_id)
            SELECT gen_random_uuid(), :url, 'PRIVATE', id
              FROM users WHERE email = 'other@example.test'
        """), {"url": FOREIGN})

        # 1. Own asset, no memory row -> must be backfilled.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            SELECT u.id, :url, :url, a.id, 'ready'
              FROM users u, content_assets a
             WHERE u.email = 'owner@example.test' AND a.canonical_url = :url
        """), {"url": OWNED})
        # 2. PUBLIC asset, no memory row -> must be backfilled.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            SELECT u.id, :url, :url, a.id, 'ready'
              FROM users u, content_assets a
             WHERE u.email = 'owner@example.test' AND a.canonical_url = :url
        """), {"url": PUBLIC})
        # 3. content_id NULL, the pre-Phase-1 shape -> must stay NULL.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            SELECT id, :url, :url, NULL, 'ready'
              FROM users WHERE email = 'owner@example.test'
        """), {"url": LEGACY})
        # 4. HOSTILE: the owner's item pointing at ANOTHER user's PRIVATE asset.
        #    Backfilling this would hand the owner a memory of private content
        #    that is not theirs.
        conn.execute(text("""
            INSERT INTO items (user_id, url, canonical_url, content_id, status)
            SELECT u.id, :url, :url, a.id, 'ready'
              FROM users u, content_assets a
             WHERE u.email = 'owner@example.test' AND a.canonical_url = :url
        """), {"url": FOREIGN})
    eng.dispose()


# --- the migration applies -------------------------------------------------

def test_running_the_chain_does_not_silence_the_application_loggers(
        admin_engine):
    """`alembic upgrade head` runs in-process on API startup, so whatever it
    does to the logging configuration happens to the running application.

    Alembic's env.py used to call fileConfig() with Python's default
    `disable_existing_loggers=True`, which sets `.disabled = True` on every
    pre-existing logger the ini does not name -- i.e. every `findback.*`
    logger. The API therefore stopped logging at all from the moment the schema
    bootstrap ran, which is invisible to any test that reads a log record
    before its own fixture migrates.

    Asserted here, next to the migrations that trigger it.
    """
    import logging
    import logging.config

    from app import database as db_module

    app_logger = logging.getLogger("findback.auth")
    privacy_logger = logging.getLogger("findback.ingest")

    admin, name, url = _fresh_db(admin_engine, "fb_p16_logging")
    eng = None
    try:
        _upgrade(url)
        assert not app_logger.disabled, (
            "alembic upgrade head disabled findback.auth")
        assert not privacy_logger.disabled, (
            "alembic upgrade head disabled findback.ingest")

        # And the same thing explicitly, against the real ini, so the claim does
        # not rest on env.py being the only caller.
        ini = os.path.join(db_module.BACKEND_DIR, "alembic.ini")
        logging.config.fileConfig(ini, disable_existing_loggers=False)
        assert not app_logger.disabled, (
            "the application logger is disabled after a migration")
        assert app_logger.isEnabledFor(logging.ERROR), (
            "the application logger cannot emit after a migration")
    finally:
        app_logger.disabled = False
        privacy_logger.disabled = False
        _drop(admin, name, eng)


def test_the_migration_applies_to_an_empty_database(admin_engine):
    """The ordinary deployment path."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_empty")
    eng = None
    try:
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            constraint = conn.execute(text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conname = 'items_user_content_fk'")).scalar()
        assert constraint is True, "the constraint was not validated"
    finally:
        _drop(admin, name, eng)


def test_the_migration_applies_to_a_non_empty_database(admin_engine):
    """The case that matters. An earlier version of this revision added the
    constraint and ran VALIDATE, which failed here:

        DETAIL: Key (user_id, content_id)=(...) is not present in table
                "user_memories".

    `NOT VALID` does not help: it defers the check to VALIDATE, which is
    exactly where the data has to be legal.
    """
    admin, name, url = _fresh_db(admin_engine, "fb_p16_full")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        _upgrade(url)  # must not raise

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conname = 'items_user_content_fk'")).scalar() is True
    finally:
        _drop(admin, name, eng)


def test_the_backfill_grants_a_memory_only_where_it_is_allowed(admin_engine):
    """The security half. The owner's own asset and the PUBLIC asset get a
    memory; the other user's PRIVATE asset does not."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_backfill")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        _upgrade(url)

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT i.canonical_url,
                       i.content_id IS NULL AS unlinked,
                       EXISTS (SELECT 1 FROM user_memories m
                                WHERE m.user_id = i.user_id
                                  AND m.content_id = i.content_id
                                  AND i.content_id IS NOT NULL) AS has_memory
                  FROM items i ORDER BY i.canonical_url
            """)).all()

        state = {r[0]: (r[1], r[2]) for r in rows}
        assert state[OWNED] == (False, True), (
            "the owner's own asset did not get a memory")
        assert state[PUBLIC] == (False, True), (
            "the PUBLIC asset did not get a memory")
        assert state[LEGACY] == (True, False), (
            "a NULL content_id must stay NULL and stay exempt")
        assert state[FOREIGN] == (True, False), (
            "an item pointing at another user's PRIVATE asset must be "
            "unlinked, never given a memory: that would be the leak this "
            "whole migration exists to prevent")
    finally:
        _drop(admin, name, eng)


def test_no_memory_is_created_for_a_cross_user_private_asset(admin_engine):
    """The same claim stated directly against the memory table, so it cannot
    pass merely because the item happened to be unlinked for another reason."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_nomem")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        _upgrade(url)

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            leaked = conn.execute(text("""
                SELECT count(*) FROM user_memories m
                  JOIN content_assets a ON a.id = m.content_id
                  JOIN users u ON u.id = m.user_id
                 WHERE a.visibility <> 'PUBLIC'
                   AND a.owner_user_id IS DISTINCT FROM m.user_id
            """)).scalar()
        assert leaked == 0, (
            "the migration created a memory of private content owned by "
            "somebody else")
    finally:
        _drop(admin, name, eng)


def test_the_backfill_preserves_the_original_save_timestamps(admin_engine):
    """The backfilled row must describe the save that already happened, not
    the moment the migration ran."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_times")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (email) VALUES ('t@example.test')"))
            conn.execute(text("""
                INSERT INTO content_assets (canonical_url, visibility,
                                             owner_user_id)
                SELECT :url, 'UNKNOWN', id FROM users
                 WHERE email = 't@example.test'
            """), {"url": OWNED})
            conn.execute(text("""
                INSERT INTO items (user_id, url, canonical_url, content_id,
                                   status, created_at, last_seen_at)
                SELECT u.id, :url, :url, a.id, 'ready',
                       now() - interval '10 days', now() - interval '2 days'
                  FROM users u, content_assets a
                 WHERE u.email = 't@example.test' AND a.canonical_url = :url
            """), {"url": OWNED})
        eng.dispose()
        eng = None

        _upgrade(url)

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT m.first_saved_at, m.last_saved_at, m.save_count,
                       i.created_at, i.last_seen_at
                  FROM user_memories m JOIN items i
                    ON i.user_id = m.user_id AND i.content_id = m.content_id
            """)).one()
        assert row[0] == row[3], "first_saved_at was reset to the migration time"
        assert row[1] == row[4], "last_saved_at was reset to the migration time"
        assert row[2] == 1, row[2]
    finally:
        _drop(admin, name, eng)


def test_the_backfill_does_not_duplicate_an_existing_memory(admin_engine):
    """A database that already has the memory row must not gain a second one.
    The UNIQUE(user_id, content_id) constraint would reject it, so an
    ON CONFLICT clause is required rather than optional."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_dup")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            # Pre-create the memory the migration would otherwise insert.
            conn.execute(text("""
                INSERT INTO user_memories (user_id, content_id, save_count)
                SELECT u.id, a.id, 7
                  FROM users u, content_assets a
                 WHERE u.email = 'owner@example.test'
                   AND a.canonical_url = :url
            """), {"url": OWNED})
        eng.dispose()
        eng = None

        _upgrade(url)  # must not raise a UniqueViolation

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            count, save_count = conn.execute(text("""
                SELECT count(*), max(save_count) FROM user_memories m
                  JOIN content_assets a ON a.id = m.content_id
                 WHERE a.canonical_url = :url
            """), {"url": OWNED}).one()
        assert count == 1, f"the backfill created {count} memory rows"
        assert save_count == 7, "the existing save_count was overwritten"
    finally:
        _drop(admin, name, eng)


def test_two_items_of_one_asset_share_a_single_memory(admin_engine):
    """A user can hold two item rows for one asset; the composite FK needs only
    one memory row to satisfy both."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_twoitems")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO items (user_id, url, canonical_url, content_id,
                                   status)
                SELECT u.id, 'https://example.test/second-url',
                       'https://example.test/second-url', a.id, 'ready'
                  FROM users u, content_assets a
                 WHERE u.email = 'owner@example.test'
                   AND a.canonical_url = :url
            """), {"url": OWNED})
        eng.dispose()
        eng = None

        _upgrade(url)

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            items, memories = conn.execute(text("""
                SELECT count(*), (SELECT count(*) FROM user_memories)
                  FROM items
            """)).one()
        assert items == 5, items
        assert memories == 2, (
            f"expected one memory per reusable asset, got {memories}")
    finally:
        _drop(admin, name, eng)


# --- after the migration --------------------------------------------------

def test_the_constraint_refuses_a_new_cross_user_item(admin_engine):
    """The guarantee holds for writes made AFTER the migration too, not just
    for the rows that existed before it."""
    from sqlalchemy.exc import IntegrityError

    admin, name, url = _fresh_db(admin_engine, "fb_p16_after")
    eng = None
    try:
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (email) VALUES ('x@example.test'), "
                "('y@example.test')"))
            conn.execute(text("""
                INSERT INTO content_assets (canonical_url, visibility,
                                             owner_user_id)
                SELECT 'https://example.test/y-private', 'PRIVATE', id
                  FROM users WHERE email = 'y@example.test'
            """))

        from sqlalchemy.orm import sessionmaker
        import app.models as models

        Session = sessionmaker(bind=eng)
        with Session() as s:
            asset_id = s.execute(text("""
                SELECT id FROM content_assets
                 WHERE canonical_url = 'https://example.test/y-private'
            """)).scalar()
            intruder = s.execute(text(
                "SELECT id FROM users WHERE email = 'x@example.test'")).scalar()
            s.add(models.Item(user_id=intruder,
                              url="https://example.test/y-private",
                              canonical_url="https://example.test/y-private",
                              content_id=asset_id, status="pending"))
            with pytest.raises(IntegrityError) as caught:
                s.commit()
            assert "items_user_content_fk" in str(caught.value), caught.value
            s.rollback()
    finally:
        _drop(admin, name, eng)


def test_a_legacy_item_with_a_null_content_id_still_works(admin_engine):
    """MATCH SIMPLE: a composite FK is satisfied when any column is NULL. This
    is what lets the constraint be added at all to a database whose items were
    never linked to an asset."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_nullok")
    eng = None
    try:
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('n@example.test')"))
            conn.execute(text("""
                INSERT INTO items (user_id, url, canonical_url, content_id,
                                   status)
                SELECT id, 'https://example.test/n', 'https://example.test/n',
                       NULL, 'ready'
                  FROM users WHERE email = 'n@example.test'
            """))
            # And one more, added after the constraint is in place.
            conn.execute(text("""
                INSERT INTO items (user_id, url, canonical_url, content_id,
                                   status)
                SELECT id, 'https://example.test/n2',
                       'https://example.test/n2', NULL, 'ready'
                  FROM users WHERE email = 'n@example.test'
            """))
    finally:
        _drop(admin, name, eng)


# --- the whole chain, and the way back ------------------------------------

def test_the_whole_chain_applies_to_an_empty_database(admin_engine):
    """0001 -> 0010 in one go, which is what `alembic upgrade head` does."""
    admin, name, url = _fresh_db(admin_engine, "fb_p16_chain")
    eng = None
    try:
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            version = conn.execute(text(
                "SELECT version_num FROM alembic_version")).scalar()
        assert version == "0014_collections", version
    finally:
        _drop(admin, name, eng)


def test_the_migration_downgrades_and_the_chain_reapplies(admin_engine):
    """A migration that cannot be run backwards is not finished.

    The downgrade loses `users.auth_subject`, which is documented in the
    revision: a subject can only be learned from a token, never from SQL, so
    the next login has to bind again. That is a real cost of downgrading and it
    is accepted rather than hidden.
    """
    from alembic import command

    admin, name, url = _fresh_db(admin_engine, "fb_p16_down")
    eng = None
    try:
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (email, auth_subject) "
                "VALUES ('d@example.test', 'sub-d')"))
        eng.dispose()
        eng = None

        cfg, url = _alembic(url)
        cfg.set_main_option("sqlalchemy.url", url)
        command.downgrade(cfg, "0009_hybrid_search")

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'items_user_content_fk'")).scalar() == 0, (
                "the constraint survived the downgrade")
            assert conn.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'auth_subject'"
            )).scalar() == 0, "the column survived the downgrade"
        eng.dispose()
        eng = None

        # And the whole chain applies again on top.
        _upgrade(url)
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() \
                == "0014_collections"
            # The backfilled memories are ordinary data and must survive.
            assert conn.execute(text(
                "SELECT count(*) FROM user_memories")).scalar() == 0
    finally:
        _drop(admin, name, eng)


def test_the_downgrade_does_not_delete_the_backfilled_memories(admin_engine):
    """Downgrading removes the GUARANTEE, not the data. The memories are rows
    the application creates on every save anyway."""
    from alembic import command

    admin, name, url = _fresh_db(admin_engine, "fb_p16_downdata")
    eng = None
    try:
        _upgrade(url, "0009_hybrid_search")
        _seed_the_hostile_rows(url)
        _upgrade(url)

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            before = conn.execute(text(
                "SELECT count(*) FROM user_memories")).scalar()
        eng.dispose()
        eng = None
        assert before == 2, before

        cfg, url = _alembic(url)
        cfg.set_main_option("sqlalchemy.url", url)
        command.downgrade(cfg, "0009_hybrid_search")

        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            after = conn.execute(text(
                "SELECT count(*) FROM user_memories")).scalar()
        assert after == before, (
            f"the downgrade deleted user data: {before} -> {after}")
    finally:
        _drop(admin, name, eng)
