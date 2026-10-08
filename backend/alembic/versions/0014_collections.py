"""Private collections and owner-enforced membership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0014_collections'
down_revision = '0013_job_attempt_token'
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint('items_id_user_uq', 'items', ['id', 'user_id'])
    op.create_table('collections',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('id', 'user_id', name='collections_id_user_uq'))
    op.create_index('collections_user_idx', 'collections', ['user_id'])
    op.create_table('collection_items',
        sa.Column('collection_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('item_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['collection_id', 'user_id'], ['collections.id', 'collections.user_id'], ondelete='CASCADE', name='collection_items_owner_fk'),
        sa.ForeignKeyConstraint(['item_id', 'user_id'], ['items.id', 'items.user_id'], ondelete='CASCADE', name='collection_items_item_owner_fk'))
    op.create_index('collection_items_item_idx', 'collection_items', ['item_id', 'user_id'])
    for table in ('collections', 'collection_items'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON {table} FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN REVOKE ALL ON {table} FROM {role}; END IF; END $$")


def downgrade():
    op.drop_table('collection_items')
    op.drop_table('collections')
    op.drop_constraint('items_id_user_uq', 'items', type_='unique')
