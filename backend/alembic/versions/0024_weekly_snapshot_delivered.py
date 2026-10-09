"""Whether a claimed week actually reached a device.

The claim is committed before delivery is attempted, so a claim can be stranded
by a hard crash that runs no handler at all: SIGKILL, an OOM kill, the host
going away. Nothing else in the tick would ever free it.

`delivered_at` is the record of the outcome the claim exists to protect. It is
set when at least one device accepts the note, which is exactly the condition
under which the claim must be kept -- so a row with `delivered_at IS NULL` and
an old `created_at` is a claim nobody delivered and can be given back.

Deliberately a timestamp rather than a boolean: it answers "when did the note
land", which is what an operator asks first, and `IS NOT NULL` does the
boolean's job on its own. Left NULL for every claim that has not been confirmed
delivered, so it is backfill-free and needs no default.

`created_at` already records when the claim was taken, so the staleness test
needs no second column.
"""
from alembic import op
import sqlalchemy as sa

revision = '0024_weekly_snapshot_delivered'
down_revision = '0023_device_tokens'
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name'] for column in
               sa.inspect(op.get_bind()).get_columns('weekly_snapshots')}
    if 'delivered_at' not in columns:
        op.add_column('weekly_snapshots',
                      sa.Column('delivered_at', sa.DateTime(timezone=True),
                                nullable=True))


def downgrade():
    op.drop_column('weekly_snapshots', 'delivered_at')