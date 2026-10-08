"""One absolute-time reminder per saved memory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0018_reminders'
down_revision = '0017_reprocess_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    if 'reminders' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('reminders',
            sa.Column('item_id', UUID(as_uuid=True), sa.ForeignKey('items.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('time_zone', sa.String(100), nullable=False),
            sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_table('reminders')
