"""Hybrid lexical retrieval (Phase 12).

Adds:

    items.search_text     the whole searchable document (title, brief,
                          highlights, entities, topics, structured_data)
    items.search_text_tsv its full-text index
    chunks.tsv            the chunk's own full-text index

`items.tsv` already exists but covers only title + summary + tags, which is why
a word remembered from a recipe's ingredients was unreachable. It is left alone:
it is the original index and nothing depends on removing it.

Both new tsvectors are generated columns rather than plain ones, so they cannot
drift from the text they index.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_hybrid_search"
down_revision = "0008_chunk_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("search_text", sa.Text()))
    # to_tsvector('english', ...) is IMMUTABLE for a constant config, so a
    # STORED generated column is legal here. Added as SQL because the generated
    # clause cannot be expressed through op.add_column.
    op.execute("""
        ALTER TABLE items
        ADD COLUMN search_text_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(search_text, '')))
        STORED
    """)
    op.execute("CREATE INDEX items_search_text_tsv_idx ON items "
               "USING gin (search_text_tsv)")
    op.execute("""
        ALTER TABLE chunks
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(chunk_text, '')))
        STORED
    """)
    op.execute("CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv)")

    # Memories saved before this revision have no search_text. Backfilling from
    # what they do have is better than leaving them invisible to every lexical
    # query: an older save should still be findable by its title.
    op.execute("""
        UPDATE items
        SET search_text = concat_ws(' ',
            coalesce(title_clean, ''), coalesce(summary, ''),
            coalesce(key_points::text, ''))
        WHERE status = 'ready' AND search_text IS NULL
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS chunks_tsv_idx")
    op.execute("DROP INDEX IF EXISTS items_search_text_tsv_idx")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS search_text_tsv")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS search_text")