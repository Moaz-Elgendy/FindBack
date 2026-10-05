"""Add Brief v2 and evidence without changing legacy item fields."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0012_brief_v2'
down_revision = '0011_job_claim_time'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("evidence_bundle", JSONB, nullable=False,
                                    server_default=sa.text("'{}'::jsonb")))
    op.add_column("items", sa.Column("brief_v2", JSONB, nullable=False,
                                    server_default=sa.text("'{}'::jsonb")))
    op.add_column("items", sa.Column("processing_metadata", JSONB, nullable=False,
                                    server_default=sa.text("'{}'::jsonb")))
    op.add_column("items", sa.Column("needs_retry", sa.Boolean, nullable=False,
                                    server_default=sa.text("false")))
    op.create_index('items_evidence_source_idx', 'items',
                    [sa.text("(evidence_bundle->>'source_platform')"),
                     sa.text("(evidence_bundle->>'source_id')")],
                    postgresql_where=sa.text("evidence_bundle->>'evidence_level' = 'full_transcript'"))


def downgrade():
    op.drop_index('items_evidence_source_idx', table_name='items')
    for name in ('needs_retry', 'processing_metadata', 'brief_v2', 'evidence_bundle'):
        op.drop_column('items', name)
