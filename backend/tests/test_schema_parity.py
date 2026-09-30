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

from app.models import TSV_EXPRESSION, Chunk, Item, User

DIALECT = postgresql.dialect()
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial.py"


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def table_ddl(model) -> str:
    return str(CreateTable(model.__table__).compile(dialect=DIALECT))


def index_ddl(model) -> dict:
    return {ix.name: str(CreateIndex(ix).compile(dialect=DIALECT)) for ix in model.__table__.indexes}


def migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def migration_columns(table: str) -> set:
    line = re.search(r'op\.create_table\("%s",.*' % table, migration_source()).group(0)
    cols = set(re.findall(r'sa\.Column\("(\w+)"', line))
    if table == "items":
        cols.add("tsv")  # added by the raw ALTER TABLE right after create_table
    return cols


@pytest.mark.parametrize("model", [User, Item, Chunk], ids=["users", "items", "chunks"])
def test_model_columns_match_migration(model):
    assert set(model.__table__.columns.keys()) == migration_columns(model.__tablename__)


def test_tsv_expression_matches_migration_verbatim():
    expr = re.search(r"ADD COLUMN tsv tsvector GENERATED ALWAYS AS \((.*?)\) STORED",
                     migration_source(), re.S).group(1)
    assert norm(TSV_EXPRESSION) == norm(expr)


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
    }


def test_chunks_indexes_match_migration():
    assert {ix.name for ix in Chunk.__table__.indexes} == {
        "chunks_item_idx", "chunks_embedding_hnsw",
    }


def test_tsv_index_is_gin():
    # SQLAlchemy echoes the access method verbatim, so this renders lowercase.
    assert "USING gin" in index_ddl(Item)["items_tsv_idx"]


@pytest.mark.parametrize("model,index", [(Item, "items_embedding_hnsw"), (Chunk, "chunks_embedding_hnsw")])
def test_vector_indexes_are_hnsw_cosine(model, index):
    ddl = index_ddl(model)[index]
    assert "USING hnsw" in ddl
    assert "vector_cosine_ops" in ddl


def test_unique_constraint_still_guards_duplicates():
    names = {c.name for c in Item.__table__.constraints}
    assert "items_user_canonical_uq" in names
