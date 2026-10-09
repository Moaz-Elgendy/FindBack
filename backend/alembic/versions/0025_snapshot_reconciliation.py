"""Reconcile already-applied snapshot schemas without deleting history."""
from alembic import op
import sqlalchemy as sa

revision = '0025_snapshot_reconciliation'
down_revision = '0024_weekly_snapshot_delivered'
branch_labels = None
depends_on = None


def upgrade():
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys('snapshot_items'):
        if 'save_id' in foreign_key['constrained_columns']:
            op.drop_constraint(foreign_key['name'], 'snapshot_items', type_='foreignkey')
    for table in ('weekly_snapshots', 'snapshot_items'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON {table} FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                       f"THEN REVOKE ALL ON {table} FROM {role}; END IF; END $$")


def downgrade():
    # Keep privacy and historical ids: restoring the FK can reject deleted saves.
    pass
