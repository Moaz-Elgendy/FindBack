"""Keep the previous brief durably during explicit reprocessing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0017_reprocess_snapshot'
down_revision = '0016_item_actions'
branch_labels = None
depends_on = None


def upgrade():
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('items')}
    if "reprocess_snapshot" not in existing:
        op.add_column("items", sa.Column("reprocess_snapshot", JSONB(), nullable=True))
    if "reprocess_failure" not in existing:
        op.add_column("items", sa.Column("reprocess_failure", sa.Text(), nullable=True))


def downgrade():
    # Preserve the old brief if an upgrade is rolled back while processing.
    op.execute("""UPDATE items SET summary=coalesce(reprocess_snapshot->>'summary', summary),
        title_clean=coalesce(reprocess_snapshot->>'title_clean', title_clean),
        brief_v2=coalesce(reprocess_snapshot->'brief_v2', brief_v2),
        status='ready' WHERE reprocess_snapshot IS NOT NULL""")
    op.drop_column('items', 'reprocess_failure')
    op.drop_column('items', 'reprocess_snapshot')
