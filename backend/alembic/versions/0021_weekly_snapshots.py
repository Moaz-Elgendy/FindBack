"""The frozen list of saves one weekly note counted.

The note says "3 things you saved and forgot". Those three ids are stored, not
recomputed: the addendum requires the "Worth another look" screen to show
exactly what the count said, and a forgotten set that shifts between the send
and the tap would make the note a lie.

`iso_week` is added in 0022, together with the once-per-week guard.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0021_weekly_snapshots'
down_revision = '0020_forgotten_opens'
branch_labels = None
depends_on = None


def upgrade():
    if 'weekly_snapshots' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('weekly_snapshots',
            sa.Column('id', UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text('gen_random_uuid()')),
            sa.Column('user_id', UUID(as_uuid=True),
                      sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True),
                      server_default=sa.text('now()'), nullable=False))
        op.create_index('weekly_snapshots_user_idx', 'weekly_snapshots', ['user_id'])
    if 'snapshot_items' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table('snapshot_items',
            sa.Column('snapshot_id', UUID(as_uuid=True),
                      sa.ForeignKey('weekly_snapshots.id', ondelete='CASCADE'), primary_key=True),
            # Historical ids survive save deletion so the promised count cannot shrink.
            sa.Column('save_id', UUID(as_uuid=True), primary_key=True))
    for table in ('weekly_snapshots', 'snapshot_items'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON {table} FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                       f"THEN REVOKE ALL ON {table} FROM {role}; END IF; END $$")


def downgrade():
    op.drop_table('snapshot_items')
    op.drop_table('weekly_snapshots')
