"""Processing state machine: PENDING / PROCESSING / READY / FAILED (Phase 6).

New revision 0006 on top of 0005. Nothing is rewritten.

1. Job statuses are normalised to the four states this phase allows. Phase 5
   used `pending` and `dispatched`; `dispatched` has no place in the machine
   because handing work to the queue IS entering PROCESSING.

2. The "one active job" index is widened from `status = 'pending'` to
   `status IN ('PENDING','PROCESSING')`. Under the old predicate a second job
   could be created for content whose first job was already being worked on,
   which is exactly the double-processing this phase forbids.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_job_state_machine"
down_revision = "0005_processing_jobs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # 1. Normalise existing statuses into the four allowed states.
    bind.execute(sa.text(
        "UPDATE processing_jobs SET status = 'PENDING' WHERE status = 'pending'"))
    bind.execute(sa.text(
        "UPDATE processing_jobs SET status = 'PROCESSING' "
        "WHERE status = 'dispatched'"))
    # The column default must follow the states too, otherwise a brand new job
    # is created as lower-case 'pending' and no longer matches the machine.
    bind.execute(sa.text(
        "ALTER TABLE processing_jobs ALTER COLUMN status SET DEFAULT 'PENDING'"))

    # 2. Widen the active-job index to cover PROCESSING as well.
    op.drop_index("processing_jobs_active_uq", table_name="processing_jobs")
    op.create_index("processing_jobs_active_uq", "processing_jobs",
                    ["content_id", "job_type"], unique=True,
                    postgresql_where=sa.text(
                        "status IN ('PENDING', 'PROCESSING')"))


def downgrade():
    op.drop_index("processing_jobs_active_uq", table_name="processing_jobs")
    op.create_index("processing_jobs_active_uq", "processing_jobs",
                    ["content_id", "job_type"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE processing_jobs SET status = 'pending' "
        "WHERE status IN ('PENDING', 'PROCESSING')"))
    bind.execute(sa.text(
        "UPDATE processing_jobs SET status = 'dispatched' "
        "WHERE status = 'READY'"))
    bind.execute(sa.text(
        "ALTER TABLE processing_jobs ALTER COLUMN status SET DEFAULT 'pending'"))