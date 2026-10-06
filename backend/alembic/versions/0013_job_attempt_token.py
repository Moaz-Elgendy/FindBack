"""Fence writes and heartbeats from superseded worker attempts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0013_job_attempt_token'
down_revision = '0012_brief_v2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('processing_jobs', sa.Column('attempt_token', UUID(as_uuid=True)))


def downgrade():
    op.drop_column('processing_jobs', 'attempt_token')
