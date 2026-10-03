"""Explicit pipeline stages: resume where a retry failed (Phase 8).

New revision 0007 on top of 0006.

Stage progress has to survive the process that produced it, otherwise a retry
cannot know what to skip. Two things are added:

1. `processing_jobs.last_stage` -- the last stage that completed. The worker
   resumes at the stage AFTER it.

2. Per-stage artifacts on `items`, because "skip FETCH" only works if the
   fetched text is still there: `raw_text` (FETCH), `normalized_text`
   (NORMALIZE) and `chunk_texts` (CHUNK). UNDERSTAND and BRIEF already wrote
   to columns that exist.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_pipeline_stages"
down_revision = "0006_job_state_machine"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("raw_text", sa.Text()))
    op.add_column("items", sa.Column("normalized_text", sa.Text()))
    op.add_column("items", sa.Column(
        "chunk_texts", postgresql.ARRAY(sa.Text())))
    op.add_column("processing_jobs", sa.Column("last_stage", sa.String(16)))

    # An item that is already ready had its whole pipeline run before stages
    # were recorded. Marking EMBED means a retry of such an item resumes at the
    # end rather than redoing everything.
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE processing_jobs SET last_stage = 'EMBED'
        FROM items i
        WHERE i.content_id = processing_jobs.content_id
          AND i.status = 'ready'
          AND processing_jobs.last_stage IS NULL
    """))


def downgrade():
    op.drop_column("processing_jobs", "last_stage")
    op.drop_column("items", "chunk_texts")
    op.drop_column("items", "normalized_text")
    op.drop_column("items", "raw_text")