"""Multi-tenant hardening (Phase 16).

Two isolation guarantees, both enforced by the database rather than by
application code alone.

1. `users.auth_subject` -- the stable identity from the token's `sub` claim.
   Identity used to be keyed on the `email` claim, which is reassignable: when
   an address changes hands, the new holder inherits the previous user's entire
   library. The subject is what the issuer guarantees is stable.

2. A composite foreign key from `items(user_id, content_id)` to
   `user_memories(user_id, content_id)`. An application check can be forgotten
   on one route out of nine; this makes it impossible to write an item that
   points at content the user never saved, whoever wrote the code.

Migration strategy
------------------
The constraint is added only AFTER every existing row has been made legal,
because `alembic upgrade head` has to work on a NON-empty database.

An earlier version of this revision added the constraint and then ran VALIDATE,
which fails on any database holding one item with no memory row:

    [SQL: ALTER TABLE items VALIDATE CONSTRAINT items_user_content_fk]
    DETAIL: Key (user_id, content_id)=(...) is not present in table "user_memories".

`NOT VALID` does not help there. It exempts a row from being checked when the
constraint is ADDED, not from the later VALIDATE, which is exactly where the
data has to be legal.

So, in order:

1. `users.auth_subject`, nullable and unique. PostgreSQL treats NULLs as
   distinct, so accounts that predate the column do not collide with each other
   and existing rows are left alone. Nothing is backfilled here: a subject can
   only be learned from a token, so binding happens at login, not in SQL.

2. Backfill the missing memory row for every item that points at an asset its
   OWN user is allowed to reuse: their own asset, or a PUBLIC one that anybody
   may share. This mirrors `_find_reusable_asset` in app/routers/ingest.py,
   which is the only code that decides what a save may attach to.

3. Unlink whatever is still dangling. That state should be unreachable -- Phase
   4 gives every non-PUBLIC asset an owner and only ever points an item at its
   owner's copy or at a PUBLIC asset -- and if it ever occurred, the row was
   already reaching content it had no right to reach, so dropping the pointer
   is the honest repair. `items.content_id` is nullable and every read path
   already handles NULL, so nothing is deleted and the item stays searchable on
   its own text.

4. Add the constraint NOT VALID, then VALIDATE. NOT VALID keeps the ADD from
   blocking writes for the length of a full scan; VALIDATE afterwards only
   needs SHARE UPDATE EXCLUSIVE.

No ON DELETE clause, deliberately. CASCADE would mean that deleting a memory row
silently deletes the user's item rows, and those hold the derived text -- brief,
search document, vectors -- which cannot be recovered from anywhere. NO ACTION
refuses the delete instead of performing it, so the mistake is loud. The one
caller that removes a memory, `retention.delete_save`, deletes the items first.

Legacy rows
-----------
`items.content_id` is nullable, and a composite foreign key is satisfied when
ANY of its columns is NULL (SQL standard MATCH SIMPLE, the PostgreSQL default).
An item that predates Phase 1, or one unlinked by step 3, is therefore exempt
and keeps working exactly as before. This is what lets the constraint be added
to a database whose items were never linked to an asset.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_multi_tenant"
down_revision = "0009_hybrid_search"
branch_labels = None
depends_on = None


def upgrade():
    # 1. The stable identity. Nullable on purpose: see the module docstring.
    op.add_column("users", sa.Column("auth_subject", sa.String(255)))
    op.create_unique_constraint("users_auth_subject_uq", "users", ["auth_subject"])

    # 2. Give every legitimately-linked item the memory row the constraint
    #    requires. ON CONFLICT because two items can already resolve to one
    #    asset for one user, and that single memory row is the correct answer
    #    for both.
    op.execute(sa.text("""
        INSERT INTO user_memories (
            id, user_id, content_id, first_saved_at, last_saved_at,
            save_count, created_at, updated_at
        )
        SELECT gen_random_uuid(), i.user_id, i.content_id, i.created_at,
               i.last_seen_at, 1, i.created_at,
               COALESCE(i.processed_at, i.created_at)
        FROM items i
        JOIN content_assets a ON a.id = i.content_id
        WHERE (a.visibility = 'PUBLIC' OR a.owner_user_id = i.user_id)
          AND NOT EXISTS (SELECT 1 FROM user_memories m
                          WHERE m.user_id = i.user_id
                            AND m.content_id = i.content_id)
        ON CONFLICT (user_id, content_id) DO NOTHING
    """))

    # 3. Unlink the rest, and say so. RAISE NOTICE reaches the operator's
    #    alembic output; the count is deliberately not the ids.
    op.execute("""
        DO $$
        DECLARE
            unlinked integer;
        BEGIN
            UPDATE items i SET content_id = NULL
            WHERE i.content_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM user_memories m
                              WHERE m.user_id = i.user_id
                                AND m.content_id = i.content_id);
            GET DIAGNOSTICS unlinked = ROW_COUNT;
            IF unlinked > 0 THEN
                RAISE NOTICE
                    '0010_multi_tenant: unlinked % item(s) that pointed at '
                    'content their own user could not reuse', unlinked;
            END IF;
        END $$
    """)

    # 4. The isolation guarantee itself.
    op.execute("""
        ALTER TABLE items
        ADD CONSTRAINT items_user_content_fk
        FOREIGN KEY (user_id, content_id)
        REFERENCES user_memories (user_id, content_id)
        NOT VALID
    """)
    op.execute("ALTER TABLE items VALIDATE CONSTRAINT items_user_content_fk")


def downgrade():
    # Drops the guarantee and nothing else. The memories created in step 2 stay:
    # they are ordinary rows that app/routers/ingest.py creates on every save,
    # so removing them here would delete user data the downgrade cannot
    # reconstruct.
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_user_content_fk")
    # This DOES lose data: every bound subject. There is no way to recover a
    # token's `sub` from SQL, so the next login after a downgrade has to bind
    # again. Kept because a downgrade that cannot run is not a downgrade.
    op.drop_constraint("users_auth_subject_uq", "users", type_="unique")
    op.drop_column("users", "auth_subject")
