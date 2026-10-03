"""Durable processing jobs: an outbox so a save can never lose its intent (Phase 5).

New revision 0005 on top of 0004. Nothing is rewritten.

The bug this fixes: `POST /ingest` committed the save and then published to
Celery. If Redis was down the publish failed, `_enqueue` swallowed the error,
and the item stayed `pending` with nothing left to retry it -- a permanently
stuck save.

The fix is the outbox pattern. `processing_jobs` is written in the SAME
transaction as the content, so the intent is committed atomically with the
save. A separate dispatcher reads pending jobs and publishes them. The fast
path still tries to publish immediately; the job row is the safety net for when
that fails.

Uniqueness is partial on status='pending': one ACTIVE job per
(content_id, job_type). A dispatched job no longer blocks a new one, so a
re-save or a re-run can schedule new work, but two pending jobs can never
coexist for the same content and pipeline.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_processing_jobs"
down_revision = "0004_privacy_dedupe"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("content_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("content_assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("job_type", sa.String(64),
                  server_default="process_item:v1", nullable=False),
        sa.Column("status", sa.String(16),
                  server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(),
                  server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("processing_jobs_active_uq", "processing_jobs",
                    ["content_id", "job_type"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("processing_jobs_dispatch_idx", "processing_jobs",
                    ["status", "available_at"])

    # Backfill: every content that is still waiting to be processed gets one
    # pending job, so upgrading cannot leave already-stuck saves stranded.
    op.execute("""
        INSERT INTO processing_jobs (content_id, job_type, status, attempt_count,
                                     available_at, created_at, updated_at)
        SELECT DISTINCT a.id, 'process_item:v1', 'pending', 0, now(), now(), now()
        FROM content_assets a
        JOIN items i ON i.content_id = a.id
        WHERE i.status = 'pending'
    """)


def downgrade():
    op.drop_index("processing_jobs_dispatch_idx", table_name="processing_jobs")
    op.drop_index("processing_jobs_active_uq", table_name="processing_jobs")
    op.drop_table("processing_jobs")