"""Record when a processing job was claimed (Phase 18).

Why a column, rather than a counter
-----------------------------------
Phase 18 asks for `queue_wait_time`: how long a job sat before a worker picked
it up. A worker is a different OS process from the API, so a duration measured
in memory inside a worker can never appear in the API's `/metrics`. Without a
durable timestamp the metric is either impossible or a lie.

So the claim time is stored, and both durations are derived from the database
at scrape time:

    queue_wait_time      claimed_at - created_at
    processing_duration  updated_at - claimed_at

which means they cover every worker, survive a restart, and cannot silently
reset to zero.

Migration strategy
------------------
One nullable column, no backfill, no constraint change:

* nullable, because an existing row has no claim time to recover and guessing
  one would put fabricated data into a metric;
* added, not backfilled, so this applies instantly on a large `processing_jobs`
  table -- a table rewrite would need a lock nobody asked for;
* the Phase 8 `last_stage` column set the precedent of a nullable column added
  alongside observability rather than rebuilt around it.

`downgrade` drops the column. The metric disappears with it, which is the
correct behaviour for a downgrade rather than a defect.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_job_claim_time"
down_revision = "0010_multi_tenant"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("processing_jobs", sa.Column(
        "claimed_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("processing_jobs", "claimed_at")
