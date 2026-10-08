"""Retain deleted saves for the five-second Undo window."""
from alembic import op
import sqlalchemy as sa

revision = "0015_undo_delete"
down_revision = "0014_collections"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "deleted_at" not in {column["name"] for column in inspector.get_columns("items")}:
        op.add_column("items", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "items_deleted_at_idx" not in {index["name"] for index in inspector.get_indexes("items")}:
        op.create_index("items_deleted_at_idx", "items", ["deleted_at"],
                        postgresql_where=sa.text("deleted_at IS NOT NULL"))


def downgrade():
    # Pending saves become visible again; rollback never destroys their data.
    op.drop_index("items_deleted_at_idx", table_name="items")
    op.drop_column("items", "deleted_at")
