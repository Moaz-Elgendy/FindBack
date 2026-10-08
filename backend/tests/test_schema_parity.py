"""Guard against drift between `app/models.py` and the Alembic baseline.

The two used to disagree on `tsv` (plain column vs. `GENERATED ALWAYS ... STORED`)
and on JSON vs JSONB, so a DB built by `create_all` silently lacked the GIN index
that BM25 recall depends on. Everything here runs on compiled DDL — no database.
"""
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable, CreateIndex

from app.models import (
    IMMUTABLE_ARRAY_TO_STRING_SQL, TSV_EXPRESSION, Chunk, ContentAsset, Item, User,
    UserMemory,
)

DIALECT = postgresql.dialect()
MIGRATION_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = MIGRATION_DIR / "0001_initial.py"


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def table_ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def index_ddl(model) -> dict:
    return {ix.name: str(CreateIndex(ix).compile(dialect=DIALECT)) for ix in model.__table__.indexes}


def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def all_migration_source() -> str:
    """Every revision, so columns added by a later migration are still counted."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.py")))


def migration_columns(table: str) -> set:
    source = all_migration_source()
    # Take only this table's create_table(...) call, not the rest of the file.
    start = re.search(r'op\.create_table\(\s*"%s",' % re.escape(table), source)
    if not start:
        # The table is not created by a create_table call (or not yet at all).
        return set()
    chunk = source[start.start():]
    end = chunk.find("\n    op.")
    if end != -1:
        chunk = chunk[:end]
    cols = set(re.findall(r'sa\.Column\("(\w+)"', chunk))
    if table == "items":
        cols.add("tsv")  # added by the raw ALTER TABLE right after create_table
    # Columns a later revision bolted onto an existing table.
    for extra in re.findall(r'op\.add_column\(\s*"%s",\s*sa\.Column\(\s*"(\w+)"' % re.escape(table), source):
        cols.add(extra)
    # Columns added as raw SQL, which op.add_column cannot express because the
    # generated clause is not a Column. 0009 adds items.search_text_tsv and
    # chunks.tsv this way.
    for extra in re.findall(
            r'ALTER TABLE %s\s+ADD COLUMN (\w+)' % re.escape(table), source):
        cols.add(extra)
    return cols


@pytest.mark.parametrize(
    "model",
    [User, Item, Chunk, ContentAsset, UserMemory],
    ids=["users", "items", "chunks", "content_assets", "user_memories"],
)
def test_model_columns_match_migration(model):
    assert set(model.__table__.columns.keys()) == migration_columns(model.__tablename__)


def test_tsv_expression_matches_migration_verbatim():
    expr = re.search(r"ADD COLUMN tsv tsvector GENERATED ALWAYS AS \((.*?)\) STORED",
                     migration_source(), re.S).group(1)
    assert norm(TSV_EXPRESSION) == norm(expr)


def test_tsv_expression_uses_the_immutable_wrapper():
    """The builtin array_to_string is STABLE and can never be in a STORED column."""
    # Substring-safe: "array_to_string" is contained in "immutable_array_to_string".
    assert not re.search(r"(?<!immutable_)array_to_string", TSV_EXPRESSION)
    assert "immutable_array_to_string(tags,' ')" in TSV_EXPRESSION


def test_wrapper_function_ddl_matches_migration_verbatim():
    """Both schema paths must install the exact same function body."""
    block = re.search(r'IMMUTABLE_ARRAY_TO_STRING_SQL = """(.*?)"""',
                      migration_source(), re.S).group(1)
    assert norm(block) == norm(IMMUTABLE_ARRAY_TO_STRING_SQL)


def test_wrapper_is_declared_immutable_and_uses_only_immutable_operations():
    ddl = IMMUTABLE_ARRAY_TO_STRING_SQL
    assert "IMMUTABLE" in ddl
    # Nothing from the STABLE builtin family may leak back into the body.
    # Substring-safe, so the wrapper's own name does not trip the check.
    assert not re.search(r"(?<!immutable_)array_to_string", ddl)
    for stable_builtin in ("array_agg", "now(", "random("):
        assert stable_builtin not in ddl


def test_tsv_renders_as_stored_generated_column():
    ddl = table_ddl(Item)
    assert re.search(r"tsv TSVECTOR GENERATED ALWAYS AS \(to_tsvector\('english'[^\n]*\) STORED", ddl)


def test_no_plain_json_columns_remain():
    # \bJSON\b cannot match inside "JSONB" because B is a word character.
    assert re.search(r"\bJSON\b", table_ddl(Item)) is None


@pytest.mark.parametrize("column", ["fetch_metadata", "key_points", "entities"])
def test_json_fields_are_jsonb(column):
    assert isinstance(Item.__table__.columns[column].type, JSONB)


@pytest.mark.parametrize(
    "column,expression",
    [("fetch_metadata", "'{}'::jsonb"), ("key_points", "'[]'::jsonb"),
     ("entities", "'{}'::jsonb"), ("tags", "'{}'::text[]")],
)
def test_server_defaults_render_as_sql_expressions(column, expression):
    # A bare string server_default quotes the value, which would store the
    # literal text "'{}'::jsonb" as the column default instead of an empty object.
    assert f"DEFAULT {expression} " in table_ddl(Item)


def test_orm_never_writes_tsv():
    stmt = str(insert(Item).values(user_id=uuid.uuid4(), url="https://x.com/a",
                                  canonical_url="https://x.com/a").compile(dialect=DIALECT))
    assert "tsv" not in stmt


def test_items_indexes_match_migration():
    assert {ix.name for ix in Item.__table__.indexes} == {
        "items_user_created_idx", "items_category_idx", "items_tsv_idx", "items_embedding_hnsw",
        "items_search_text_tsv_idx", "items_evidence_source_idx",
        "items_deleted_at_idx",
    }


def test_chunks_indexes_match_migration():
    assert {ix.name for ix in Chunk.__table__.indexes} == {
        "chunks_item_idx", "chunks_embedding_hnsw", "chunks_tsv_idx",
    }


def test_tsv_index_is_gin():
    # SQLAlchemy echoes the access method verbatim, so this renders lowercase.
    assert "USING gin" in index_ddl(Item)["items_tsv_idx"]


@pytest.mark.parametrize("model,index", [(Item, "items_embedding_hnsw"), (Chunk, "chunks_embedding_hnsw")])
def test_vector_indexes_are_hnsw_cosine(model, index):
    ddl = index_ddl(model)[index]
    assert "USING hnsw" in ddl
    assert "vector_cosine_ops" in ddl


def test_privacy_indexes_match_migration():
    """Phase 4's uniqueness must stay privacy-scoped in both places.

    A plain UNIQUE(dedupe_key) would forbid two users from each holding a
    private copy of the same URL, which is the whole point of the phase.
    """
    assert {ix.name for ix in ContentAsset.__table__.indexes} == {
        "content_assets_public_dedupe_uq", "content_assets_owner_dedupe_uq",
        "content_assets_canonical_url_idx", "content_assets_processing_status_idx",
    }
    public = index_ddl(ContentAsset)["content_assets_public_dedupe_uq"]
    owner = index_ddl(ContentAsset)["content_assets_owner_dedupe_uq"]
    assert "UNIQUE" in public and "dedupe_key" in public
    assert "visibility = 'PUBLIC'" in public
    assert "owner_user_id, dedupe_key" in owner.replace("(", "").replace(")", "")
    assert "visibility <> 'PUBLIC'" in owner


def test_unique_constraint_still_guards_duplicates():
    names = {c.name for c in Item.__table__.constraints}
    assert "items_user_canonical_uq" in names
