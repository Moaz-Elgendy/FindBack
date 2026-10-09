"""One weekly note per user per ISO week.

The task that builds the note runs every fifteen minutes, and its window is
wider than one tick: a beat restart, a redeploy or two overlapping beat
instances can all reach the same user inside the same window. Without a unique
key each of those runs would create a snapshot and notify again, so the user
gets the same note several times.

The key is the ISO week in the user's OWN zone, not UTC: a user in Auckland
whose window is Monday 09:00 local is in a different ISO week than the same
instant is for a UTC user, and the guard has to follow the user's calendar.

`iso_week` is nullable on purpose. A snapshot created outside the weekly task
(a test, a manual call) has no week, and PostgreSQL treats NULLs as distinct,
so those rows neither collide with each other nor occupy the real weekly key.
"""
from alembic import op
import sqlalchemy as sa

revision = '0022_weekly_snapshot_week'
down_revision = '0021_weekly_snapshots'
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name'] for column in
               sa.inspect(op.get_bind()).get_columns('weekly_snapshots')}
    if 'iso_week' not in columns:
        op.add_column('weekly_snapshots',
                      sa.Column('iso_week', sa.String(8), nullable=True))
    existing = {c['name'] for c in
                sa.inspect(op.get_bind()).get_unique_constraints('weekly_snapshots')}
    if 'weekly_snapshots_user_week_uq' not in existing:
        op.create_unique_constraint('weekly_snapshots_user_week_uq',
                                    'weekly_snapshots', ['user_id', 'iso_week'])


def downgrade():
    op.drop_constraint('weekly_snapshots_user_week_uq', 'weekly_snapshots',
                       type_='unique')
    op.drop_column('weekly_snapshots', 'iso_week')