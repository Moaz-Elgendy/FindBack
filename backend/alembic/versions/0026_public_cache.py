"""Expand anonymous public cache without rewriting any existing save."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_public_cache"
down_revision = "0025_snapshot_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("regeneration_day", sa.Date()))
    op.add_column("items", sa.Column("regeneration_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("content_assets", sa.Column("cache_url", sa.Text()))
    op.add_column("content_assets", sa.Column("cache_payload", JSONB()))
    op.add_column("content_assets", sa.Column("cache_last_hit_at", sa.DateTime(timezone=True)))
    op.add_column("content_assets", sa.Column("cache_expires_at", sa.DateTime(timezone=True)))
    op.create_index("content_assets_cache_url_uq", "content_assets", ["cache_url"], unique=True)
    op.create_index("content_assets_cache_expiry_idx", "content_assets", ["cache_expires_at"])


def downgrade():
    op.drop_column("items", "regeneration_count")
    op.drop_column("items", "regeneration_day")
    op.drop_index("content_assets_cache_expiry_idx", table_name="content_assets")
    op.drop_index("content_assets_cache_url_uq", table_name="content_assets")
    for name in ("cache_expires_at", "cache_last_hit_at", "cache_payload", "cache_url"):
        op.drop_column("content_assets", name)
