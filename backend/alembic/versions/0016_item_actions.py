"""Per-save edits and an explicit link-only choice."""
from alembic import op
import sqlalchemy as sa

revision = "0016_item_actions"
down_revision = "0015_undo_delete"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('items')}
    if 'edited_title' not in existing:
        op.add_column("items", sa.Column("edited_title", sa.Text(), nullable=True))
    if 'edited_summary' not in existing:
        op.add_column("items", sa.Column("edited_summary", sa.Text(), nullable=True))
    if 'link_only' not in existing:
        op.add_column("items", sa.Column("link_only", sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade():
    # Retain user-visible edits when returning to clients without override columns.
    op.execute("""
        UPDATE items SET title = coalesce(edited_title, title),
                         title_clean = coalesce(edited_title, title_clean),
                         summary = CASE WHEN link_only THEN edited_summary
                                        ELSE coalesce(edited_summary, summary) END,
                         brief_v2 = CASE WHEN edited_summary IS NOT NULL
                             THEN jsonb_set(brief_v2, '{instant_brief}', to_jsonb(edited_summary))
                             ELSE brief_v2 END
    """)
    for name in ('link_only', 'edited_summary', 'edited_title'):
        op.drop_column('items', name)
