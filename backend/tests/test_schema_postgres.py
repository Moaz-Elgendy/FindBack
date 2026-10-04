"""Real-PostgreSQL proof that the `tsv` generated column can actually be built.

tests/test_schema_parity.py compares the ORM against the migration textually.
That is necessary but not sufficient: it passed while the schema was unbuildable,
because `array_to_string` is STABLE and a STORED generated column may only call
IMMUTABLE functions. Both `alembic upgrade head` and `SCHEMA_BOOTSTRAP=create`
failed on every Postgres version, and nothing in the suite noticed.

These tests compile the same DDL against a live server and assert the column
really exists and really indexes tag text.

Skipped unless TEST_DATABASE_URL is set, so `pytest` stays green with no
database (as in the existing CI job).

    TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/findback \
        python -m pytest tests/test_schema_postgres.py -q
"""
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateTable

from app.models import IMMUTABLE_ARRAY_TO_STRING_SQL, Item

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping live-PostgreSQL schema tests",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture(scope="module")
def installed_function(engine):
    """Install the helper the generated column calls.

    `test_function_is_declared_immutable` reads this function out of pg_proc, so
    it has to exist before that test runs. Installing it here, once per module,
    is what makes this file independent of test order: it used to be created
    only by `probe_table`, which the *later* tests in this file request, so on a
    database the suite had never touched the first test found no row and failed.
    """
    with engine.begin() as conn:
        conn.execute(text(IMMUTABLE_ARRAY_TO_STRING_SQL))
    return IMMUTABLE_ARRAY_TO_STRING_SQL


@pytest.fixture
def probe_table(installed_function, engine):
    """A throwaway table carrying the real Item DDL's generated column.

    Only the three indexed columns plus `tsv` are copied. Creating the whole
    `items` DDL would collide with its globally named constraints
    (items_user_canonical_uq) that the real table already owns.
    """
    name = f"tsv_probe_{uuid.uuid4().hex[:12]}"
    ddl = str(CreateTable(Item.__table__).compile(dialect=engine.dialect))
    # Keep the generated column definition from the compiled ORM DDL verbatim.
    m = re.search(
        r"(?P<col>tsv TSVECTOR GENERATED ALWAYS AS \(.*?\)\) STORED)", ddl, re.S
    )
    assert m, "compiled Item DDL must still carry the generated tsv column"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE {name} (
                title_clean TEXT,
                summary     TEXT,
                tags        TEXT[] NOT NULL DEFAULT '{{}}'::text[],
                user_id     UUID,
                url         TEXT,
                canonical_url TEXT,
                status      VARCHAR(16) NOT NULL DEFAULT 'pending',
                {m.group('col')}
            )
        """))
    yield name
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
def test_function_is_declared_immutable(installed_function, engine):
    with engine.connect() as conn:
        volatile, parallel, strict = conn.execute(text("""
            SELECT p.provolatile, p.proparallel, p.proisstrict
            FROM pg_proc p
            WHERE p.proname = 'immutable_array_to_string' AND p.pronargs = 2
        """)).one()
    assert volatile == "i", "generated columns require an IMMUTABLE function"
    assert parallel == "s"
    assert strict is True


def test_generated_column_actually_exists(probe_table, engine):
    with engine.connect() as conn:
        generated = conn.execute(text("""
            SELECT a.attgenerated
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = :name AND a.attname = 'tsv'
        """), {"name": probe_table}).scalar()
    assert generated == "s", "tsv must be a STORED generated column"


def test_builtin_array_to_string_is_stable_so_the_old_schema_could_not_build(engine):
    """Pin the root cause: the builtin the original expression used."""
    with engine.connect() as conn:
        volatile = conn.execute(text("""
            SELECT p.provolatile FROM pg_proc p
            WHERE p.proname = 'array_to_string' AND p.pronargs = 2
        """)).scalar()
    assert volatile == "s", "array_to_string is expected to stay STABLE"
    with engine.connect() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(text(
                "CREATE TABLE tsv_should_fail ("
                "tags text[],"
                "tsv tsvector GENERATED ALWAYS AS "
                "(to_tsvector('english', array_to_string(tags, ' '))) STORED)"
            ))
    assert "not immutable" in str(excinfo.value).lower()


def test_tag_text_is_searchable_through_tsv(probe_table, engine):
    """The regression this repair protects: tag terms must stay in BM25 recall."""
    with engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {probe_table} (user_id, url, canonical_url, title_clean,
                                       summary, tags, status)
            VALUES
              (gen_random_uuid(), 'https://a.test/r', 'https://a.test/r',
               'Chicken Cream Mushroom Risotto', 'a creamy one-pan dinner',
               ARRAY['italian', 'comfort-food'], 'ready'),
              (gen_random_uuid(), 'https://b.test/r', 'https://b.test/r',
               'Bose QC45 Review', 'noise cancelling headphones',
               ARRAY['audio', 'travel'], 'ready')
        """))
        rows = conn.execute(text(f"""
            SELECT title_clean,
                   ts_rank(tsv, plainto_tsquery('english', 'italian')) AS rank,
                   tsv @@ plainto_tsquery('english', 'italian')       AS tag_hit,
                   tsv @@ plainto_tsquery('english', 'risotto')      AS title_hit,
                   tsv @@ plainto_tsquery('english', 'cream')        AS summary_hit
            FROM {probe_table}
        """)).mappings().all()

    by_title = {r["title_clean"]: r for r in rows}
    risotto = by_title["Chicken Cream Mushroom Risotto"]
    bose = by_title["Bose QC45 Review"]

    # 'italian' appears only in the first row's tags: it must match there and
    # nowhere else, which is precisely the recall the old expression would lose.
    assert risotto["tag_hit"] is True
    assert bose["tag_hit"] is False
    assert risotto["rank"] > 0
    # Title and summary text must still be indexed as well.
    assert risotto["title_hit"] is True
    assert risotto["summary_hit"] is True


def test_empty_tag_array_is_handled(probe_table, engine):
    with engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {probe_table} (user_id, url, canonical_url, title_clean,
                                       summary, tags, status)
            VALUES (gen_random_uuid(), 'https://c.test/r', 'https://c.test/r',
                    'Plain Article', 'no tags at all', ARRAY[]::text[], 'ready')
        """))
        row = conn.execute(text(f"""
            SELECT tsv IS NOT NULL AS has_tsv,
                   tsv @@ plainto_tsquery('english', 'article') AS matches
            FROM {probe_table} WHERE canonical_url = 'https://c.test/r'
        """)).one()
    assert row.has_tsv is True
    assert row.matches is True


def test_wrapper_matches_array_to_string_semantics(engine):
    """The wrapper must be a faithful drop-in for the builtin it replaces."""
    with engine.begin() as conn:
        conn.execute(text(IMMUTABLE_ARRAY_TO_STRING_SQL))
        rows = conn.execute(text("""
            SELECT immutable_array_to_string(a, ' ') AS wrapped,
                   array_to_string(a, ' ')               AS original
            FROM (VALUES
              (ARRAY['a','b','c']::text[]), (ARRAY['solo']::text[]),
              (ARRAY[]::text[]), (ARRAY['a','','c']::text[]),
              (ARRAY['x','y','z']::text[])
            ) AS t(a)
        """)).all()
    assert rows, "expected comparison rows"
    for wrapped, original in rows:
        assert wrapped == original


def test_null_array_yields_null_like_the_builtin(engine):
    with engine.begin() as conn:
        conn.execute(text(IMMUTABLE_ARRAY_TO_STRING_SQL))
        wrapped, original = conn.execute(text("""
            SELECT immutable_array_to_string(NULL::text[], ' '),
                   array_to_string(NULL::text[], ' ')
        """)).one()
    assert wrapped == original is None


def test_alembic_built_items_column_uses_the_wrapper(engine):
    """When the real table was built by Alembic, its stored expression agrees."""
    with engine.connect() as conn:
        expr = conn.execute(text("""
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d
            JOIN pg_class c ON c.oid = d.adrelid
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.adnum
            WHERE c.relname = 'items' AND a.attname = 'tsv'
        """)).scalar()
    if expr is None:
        pytest.skip("items table not present in this database")
    # PostgreSQL re-renders the stored expression, so match on the call name
    # rather than the literal source text.
    assert "immutable_array_to_string" in expr
    assert not re.search(r"(?<!immutable_)\barray_to_string", expr)
