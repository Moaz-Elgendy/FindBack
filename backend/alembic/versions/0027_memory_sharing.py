"""Independent immutable share snapshots and recipient attribution."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0027_memory_sharing"
down_revision = "0026_public_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("display_name", sa.String(80)))
    op.add_column("items", sa.Column("shared_by", sa.String(80)))
    op.create_table("memory_shares",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.create_index("memory_shares_owner_created_idx", "memory_shares", ["user_id", "created_at"])
    op.create_table("share_redemptions",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("share_redemptions")
    op.drop_table("memory_shares")
    op.drop_column("items", "shared_by")
    op.drop_column("users", "display_name")
