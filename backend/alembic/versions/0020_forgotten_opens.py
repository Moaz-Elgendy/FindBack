"""When a save was first opened: the weekly note's "never opened" signal.

Deliberately not backfilled. A row that predates this column was never opened
as far as the data can tell, so it stays NULL and counts as forgotten
(docs/PHASES.md: "Forgotten" threshold: 7 days
and never opened).
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_forgotten_opens"
down_revision = "0019_account_settings"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('items')}
    if 'first_opened_at' not in existing:
        op.add_column("items", sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('items', 'first_opened_at')
