"""Account preferences and deleted-token protection."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0019_account_settings'
down_revision = '0018_reminders'
branch_labels = None
depends_on = None


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if 'weekly_note_preferences' not in tables:
        op.create_table('weekly_note_preferences',
            sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('weekday', sa.Integer(), nullable=False, server_default='6'),
            sa.Column('hour', sa.Integer(), nullable=False, server_default='18'),
            sa.Column('minute', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('time_zone', sa.String(100), nullable=False, server_default='UTC'))
    if 'deleted_identities' not in tables:
        op.create_table('deleted_identities', sa.Column('subject_hash', sa.String(64), primary_key=True))


def downgrade():
    op.drop_table('weekly_note_preferences')
    op.drop_table('deleted_identities')
