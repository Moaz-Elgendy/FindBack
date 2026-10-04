"""Chunk timestamps, so a search can point at a moment (Phase 11).

Adds `chunks.start_timestamp` / `chunks.start_seconds` and
`items.chunk_timestamps`.

The columns are nullable on purpose. An article has no timeline, and inventing
"00:00" for every paragraph would be a worse answer than no time at all.

Chunks written before this revision keep NULL, which reads exactly as "no
timestamp known" rather than as "starts at the beginning".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_chunk_timestamps"
down_revision = "0007_pipeline_stages"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chunks", sa.Column("start_timestamp", sa.String(16)))
    op.add_column("chunks", sa.Column("start_seconds", sa.Integer()))
    op.add_column("items", sa.Column("chunk_timestamps", postgresql.JSONB()))
    # No new index: `chunks_item_idx` already covers the join this search does.


def downgrade():
    op.drop_column("items", "chunk_timestamps")
    op.drop_column("chunks", "start_seconds")
    op.drop_column("chunks", "start_timestamp")