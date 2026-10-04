"""Privacy-aware deduplication: only PUBLIC content may be shared (Phase 4).

Migration strategy
------------------
New revision 0004 on top of 0003. Nothing is rewritten.

Steps, in order, because each depends on the last:

1. Visibility values are normalised to PUBLIC / PRIVATE / UNKNOWN (earlier
   revisions stored them lower-case).

2. `content_assets.owner_user_id` is added. A non-public asset belongs to
   exactly one user; a PUBLIC asset has no owner and may be reused by anyone.
   Without an owner column "PRIVATE means private" cannot be expressed: the
   database could not tell my own copy of a URL from someone else's.

3. The global UNIQUE(dedupe_key) is dropped. It has to go BEFORE the split
   below, because two users holding their own copy of the same content means
   two rows sharing one dedupe_key -- exactly what that constraint forbids.

4. Assets that Phase 2 merged across users are SPLIT BACK per user. Phase 2
   deliberately collapsed two users' saves of one URL into a shared asset.
   That was legal then, but those assets are UNKNOWN, and UNKNOWN is treated
   exactly like PRIVATE, so leaving them shared would hand one user's content
   to another. Each user gets their own copy and only their own rows are
   repointed at it; user_memories are re-pointed, never deleted or merged, so
   no user data is lost.

5. Each remaining non-PUBLIC asset is stamped with its owner's id.

6. Two privacy-scoped partial unique indexes replace the dropped constraint:
     UNIQUE (dedupe_key)                 WHERE visibility = 'PUBLIC'
     UNIQUE (owner_user_id, dedupe_key)  WHERE visibility <> 'PUBLIC'
"""
from alembic import op
import json
import sqlalchemy as sa
import uuid
from sqlalchemy.dialects import postgresql

revision = "0004_privacy_dedupe"
down_revision = "0003_content_dedupe"
branch_labels = None
depends_on = None

_ASSET_COLUMNS = (
    "dedupe_key", "canonical_url", "source_type", "visibility",
    "processing_status", "processing_error", "raw_content", "title", "brief",
    "structured_data", "entities", "topics", "intent", "pipeline_version",
    "processed_at", "created_at", "updated_at",
)
_JSON_COLUMNS = ("structured_data", "entities")


def upgrade():
    bind = op.get_bind()

    # 1. Normalise visibility to upper case.
    bind.execute(sa.text(
        "UPDATE content_assets SET visibility = upper(visibility) "
        "WHERE visibility IS NOT NULL"))
    bind.execute(sa.text(
        "UPDATE content_assets SET visibility = 'UNKNOWN' "
        "WHERE visibility IS NULL"))
    bind.execute(sa.text(
        "ALTER TABLE content_assets ALTER COLUMN visibility "
        "SET DEFAULT 'UNKNOWN'"))

    # 2. Owner column: PUBLIC assets stay ownerless, everything else is owned.
    op.add_column("content_assets", sa.Column(
        "owner_user_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("content_assets_owner_user_id_fk", "content_assets",
                          "users", ["owner_user_id"], ["id"], ondelete="CASCADE")

    # 3. Drop the global constraint BEFORE splitting (see module docstring).
    op.drop_constraint("content_assets_dedupe_key_uq", "content_assets",
                       type_="unique")

    # 4. Un-share assets that Phase 2 merged.
    _split_shared_assets(bind)

    # 5. Stamp the owner on every remaining non-PUBLIC asset.
    bind.execute(sa.text("""
        UPDATE content_assets a SET owner_user_id = m.user_id
        FROM user_memories m
        WHERE m.content_id = a.id AND a.visibility <> 'PUBLIC'
          AND a.owner_user_id IS NULL
    """))

    # 6. Privacy-scoped partial unique indexes.
    op.create_index("content_assets_public_dedupe_uq", "content_assets",
                    ["dedupe_key"], unique=True,
                    postgresql_where=sa.text("visibility = 'PUBLIC'"))
    op.create_index("content_assets_owner_dedupe_uq", "content_assets",
                    ["owner_user_id", "dedupe_key"], unique=True,
                    postgresql_where=sa.text("visibility <> 'PUBLIC'"))
def _split_shared_assets(bind) -> None:
    """Give every user their own copy of a non-PUBLIC asset Phase 2 shared."""
    shared = bind.execute(sa.text("""
        SELECT a.id,
               (SELECT count(DISTINCT m.user_id) FROM user_memories m
                 WHERE m.content_id = a.id) AS user_count
        FROM content_assets a
        WHERE a.visibility <> 'PUBLIC'
    """)).mappings().all()

    column_list = ", ".join(_ASSET_COLUMNS)
    value_list = ", ".join(
        f"CAST(:{c} AS jsonb)" if c in _JSON_COLUMNS else f":{c}"
        for c in _ASSET_COLUMNS)
    insert_sql = (
        f"INSERT INTO content_assets (id, owner_user_id, {column_list}) "
        f"VALUES (:new_id, :owner, {value_list})")

    for row in shared:
        if row["user_count"] <= 1:
            continue
        members = bind.execute(sa.text(
            "SELECT DISTINCT user_id FROM user_memories WHERE content_id = :cid"),
            {"cid": row["id"]}).scalars().all()
        values = bind.execute(sa.text(
            f"SELECT {column_list} FROM content_assets WHERE id = :cid"),
            {"cid": row["id"]}).mappings().one()
        for user_id in members:
            params = {c: values[c] for c in _ASSET_COLUMNS}
            # A Python UUID: sa.text("gen_random_uuid()") cannot be a bind value.
            params["new_id"] = uuid.uuid4()
            params["owner"] = user_id
            # JSONB comes back as a dict, which psycopg2 cannot bind; send text
            # and let the CAST above turn it into JSONB.
            for column in _JSON_COLUMNS:
                params[column] = json.dumps(params[column])
            bind.execute(sa.text(insert_sql), params)
            # Only THIS user's rows move; the others keep their own copies.
            bind.execute(sa.text(
                "UPDATE user_memories SET content_id = :new WHERE "
                "content_id = :old AND user_id = :u"),
                {"new": params["new_id"], "old": row["id"], "u": user_id})
            bind.execute(sa.text(
                "UPDATE items SET content_id = :new WHERE "
                "content_id = :old AND user_id = :u"),
                {"new": params["new_id"], "old": row["id"], "u": user_id})
        bind.execute(sa.text("DELETE FROM content_assets WHERE id = :cid"),
                     {"cid": row["id"]})
def downgrade():
    op.drop_index("content_assets_owner_dedupe_uq", table_name="content_assets")
    op.drop_index("content_assets_public_dedupe_uq", table_name="content_assets")
    # Per-user copies now share a dedupe_key, so the global unique constraint
    # can only be restored by re-merging them first.
    for table in ("items", "user_memories"):
        bind = op.get_bind()
        bind.execute(sa.text(f"""
            UPDATE {table} SET content_id = k.survivor_id
            FROM (
                SELECT loser.id AS loser_id,
                       (SELECT s.id FROM content_assets s
                         WHERE s.dedupe_key = loser.dedupe_key
                         ORDER BY s.created_at, s.id LIMIT 1) AS survivor_id
                FROM content_assets loser WHERE loser.dedupe_key IS NOT NULL
            ) k
            WHERE {table}.content_id = k.loser_id
        """))
    op.get_bind().execute(sa.text("""
        DELETE FROM content_assets a
        WHERE a.dedupe_key IS NOT NULL
          AND EXISTS (SELECT 1 FROM content_assets s
                      WHERE s.dedupe_key = a.dedupe_key
                        AND (s.created_at, s.id) < (a.created_at, a.id))
    """))
    op.create_unique_constraint("content_assets_dedupe_key_uq", "content_assets",
                                ["dedupe_key"])
    op.drop_constraint("content_assets_owner_user_id_fk", "content_assets",
                       type_="foreignkey")
    op.drop_column("content_assets", "owner_user_id")
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE content_assets SET visibility = lower(visibility)"))
    bind.execute(sa.text(
        "ALTER TABLE content_assets ALTER COLUMN visibility SET DEFAULT 'unknown'"))
