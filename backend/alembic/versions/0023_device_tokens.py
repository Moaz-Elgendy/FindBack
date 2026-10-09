"""Where this user's devices are, so a notification can be delivered later.

`token` is UNIQUE rather than unique per user, which is the whole design in one
line: a device belongs to whichever account is signed in on it right now. The
same phone is used by whoever holds the device, and its push registration is a
property of the device, not of the person. So the row moves between accounts on
sign-in rather than accumulating one row per person who ever used the phone.

Moving between accounts is BY DESIGN, not an oversight: registering a token
that another account holds reassigns it, and deleting one only ever removes the
caller's own registration. The consequence is that a user who learns somebody
else's token can take it over and receive that account's notes -- so the lock
screen text carries a count and never a title, and the note's contents are only
ever fetched per-account over an authenticated endpoint.

That also settles what a stale registration means: there is at most one row per
device, and the app re-registers on every sign-in, so an account that never
opens the app again simply stops receiving anything.

Nothing is sent from here. This is storage only; the transport that reads these
rows is a later phase.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0023_device_tokens'
down_revision = '0022_weekly_snapshot_week'
branch_labels = None
depends_on = None


def upgrade():
    if 'device_tokens' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('device_tokens',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        # ON DELETE CASCADE so deleting an account cannot leave a token behind
        # that a later push would try to deliver somebody's notifications to.
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # Named explicitly rather than via `unique=True`, so the constraint has
        # the same name in the model and in the migration and cannot drift.
        sa.UniqueConstraint('token', name='device_tokens_token_uq'),
        # The column is a closed vocabulary, so the database rejects anything
        # else: the routes validate it too, but a check that can be forgotten
        # is not a constraint.
        sa.CheckConstraint("platform IN ('android', 'ios')",
                           name='device_tokens_platform_ck'))
    op.create_index('device_tokens_user_idx', 'device_tokens', ['user_id'])
    # A push token is a live credential for this user's notifications, and the
    # backend connects as the owner while Supabase client roles must not be
    # able to read them directly. Same treatment as `collections`.
    op.execute('ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON device_tokens FROM PUBLIC')
    for role in ('anon', 'authenticated'):
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                   f"THEN REVOKE ALL ON device_tokens FROM {role}; END IF; END $$")


def downgrade():
    op.drop_table('device_tokens')