"""Dispatcher for durable processing jobs (Phase 5).

Reads `processing_jobs` rows left behind by a failed Celery publish and
publishes them to the EXISTING Celery queue. Nothing here replaces Celery or
Redis: publishing is still `process_item.delay(item_id)`.

Why this exists
---------------
`POST /ingest` commits the save first and publishes second. If Redis was down
at that moment the publish failed, the error was swallowed, and the item stayed
`pending` forever with nothing left to retry it. Because the job row is written
in the same transaction as the save, the intent survives that outage and this
dispatcher can finish the job once the queue is healthy again.

The lifecycle is the Phase 6 state machine: PENDING -> PROCESSING -> READY, or
FAILED. This module owns the durable side of it (claiming and completion); the
worker owns the content side.

Failure handling
----------------
A failed publish is not lost: `attempt_count` increments, `last_error` records
why, and `available_at` moves into the future (exponential backoff, capped) so
the dispatcher retries with a delay instead of hammering a dead queue.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import event, or_, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Item,
    JOB_ACTIVE_STATUSES, JOB_STATUS_FAILED, JOB_STATUS_PENDING,
    JOB_STATUS_PROCESSING, JOB_STATUS_READY, ProcessingJob,
)
from app.services.limits import _env_int


log = logging.getLogger("findback.outbox")

# A job locked longer than this is treated as abandoned (the dispatcher died
# mid-publish) and becomes eligible again.
LOCK_TIMEOUT = timedelta(minutes=5)
BASE_BACKOFF = timedelta(seconds=2)
MAX_BACKOFF = timedelta(minutes=5)
# Attempts before a job is declared FAILED. Bounded so nothing retries forever.
DEFAULT_MAX_ATTEMPTS = 5


def backoff_for(attempt_count: int) -> timedelta:
    """Exponential backoff, capped. attempt_count is the number of failures.

    The exponent is clamped before the shift: `timedelta * (2 ** 64)` raises
    OverflowError, which a large or corrupt attempt_count could otherwise
    trigger inside a migration or a dispatcher tick.
    """
    exponent = min(max(0, attempt_count - 1), 20)
    delay = BASE_BACKOFF * (2 ** exponent)
    return min(delay, MAX_BACKOFF)


def record_job(db, content_id, job_type: str):
    """Record the intent to process this content, in the caller's transaction.

    Written as INSERT ... ON CONFLICT DO NOTHING against the partial unique
    index rather than catching an IntegrityError: a caught unique violation
    still leaves the session flagged for rollback, which would then break the
    caller's commit. This form simply keeps the job that already exists.

    Returns the pending job for this (content, pipeline), creating it if absent.
    """
    db.execute(text("""
        INSERT INTO processing_jobs (content_id, job_type)
        VALUES (:cid, :job_type)
        ON CONFLICT (content_id, job_type)
        WHERE status IN ('PENDING', 'PROCESSING')
        DO NOTHING
    """), {"cid": str(content_id), "job_type": job_type})
    return db.query(ProcessingJob).filter(
        ProcessingJob.content_id == content_id,
        ProcessingJob.job_type == job_type,
        ProcessingJob.status.in_(JOB_ACTIVE_STATUSES),
    ).first()


def mark_processing(db, job_id) -> None:
    """The job is on the queue: the content is now occupied by this work."""
    db.query(ProcessingJob).filter(ProcessingJob.id == job_id, or_(
        ProcessingJob.status == JOB_STATUS_PENDING,
        ProcessingJob.attempt_token.is_(None))).update({
        ProcessingJob.status: JOB_STATUS_PROCESSING,
        ProcessingJob.locked_at: None,
        ProcessingJob.attempt_token: None,
        ProcessingJob.last_error: None,
        ProcessingJob.updated_at: text("now()"),
    })


def _pending_item_ids(db, content_id) -> list:
    """Item ids to publish for a content.

    A ContentAsset can back more than one item (one per user, for PUBLIC
    content), and the existing Celery task works per item, so every item still
    awaiting processing is published.

    `failed` belongs in that set, and it is the bug that made a save unfixable.
    A worker attempt that raised leaves the item `failed` and `record_failure`
    puts the job back to PENDING so it can be retried -- the only thing left
    that can publish it is this dispatcher. Looking at `pending` alone found
    nothing, and the job was then marked PROCESSING without a message ever
    going out, which is unrecoverable: the dispatcher only ever claims PENDING
    jobs.

    `processing` is deliberately excluded. It means a worker owns this content
    right now, and republishing it would start a second run for work already in
    flight.
    """
    return [row[0] for row in db.execute(text(
        "SELECT id FROM items WHERE content_id = :cid AND deleted_at IS NULL AND NOT link_only "
        "AND ((edited_title IS NULL AND edited_summary IS NULL) OR reprocess_snapshot IS NOT NULL) "
        "AND (status IN ('pending', 'failed') OR (status = 'ready' AND (needs_retry OR reprocess_snapshot IS NOT NULL)))"),
        {"cid": str(content_id)})]


def claim_job(db, job_id, lock_timeout_seconds: float = 300.0):
    """Atomically take exclusive ownership of a job. True only for the winner.

    A conditional UPDATE, not SELECT-then-UPDATE: racing workers all issue the
    statement, PostgreSQL serialises them on the row lock, and only the one that
    actually changes a row owns the work. The loser must not process.

    Claimable from PENDING (never published) or PROCESSING (published by the
    fast path or the dispatcher), and only while the lock is free. A lock older
    than `lock_timeout_seconds` is treated as abandoned by a dead worker, so a
    crashed run is retried instead of blocking the content forever.

    Phase 18 stamps `claimed_at`, but only on the transition into PROCESSING and
    only when it is not already set. COALESCE matters twice over: a retry of a
    job that was already claimed keeps the ORIGINAL claim time, so
    `queue_wait_time` answers "how long did this wait before anyone started it"
    rather than "how long since the last attempt" -- and a second racing worker
    cannot overwrite the winner's stamp, because its UPDATE matches no row.
    """
    result = db.execute(text("""
        UPDATE processing_jobs
        SET status = 'PROCESSING',
            attempt_token = gen_random_uuid(),
            locked_at = now(),
            claimed_at = COALESCE(claimed_at, now()),
            updated_at = now()
        WHERE id = :id
          AND status IN ('PENDING', 'PROCESSING')
          AND (locked_at IS NULL AND (status = 'PENDING' OR attempt_token IS NULL)
               OR locked_at < now() - make_interval(secs => :lock_secs))
    """), {"id": str(job_id), "lock_secs": lock_timeout_seconds})
    db.commit()
    return result.rowcount == 1


class AttemptLost(Exception):
    """This worker no longer owns the job; all uncommitted writes must roll back."""


def fence_attempt(db, job_id, attempt_token) -> None:
    """Check ownership under a row lock before each worker transaction commits.

    Recovery takes the same lock, so either this transaction commits before
    recovery or its superseded writes are rejected. This also covers commits
    made inside pipeline stages, rather than only the final job update.
    """
    def check(session, *args):
        with session.no_autoflush:
            token = session.execute(text(
                'SELECT attempt_token FROM processing_jobs WHERE id = :id FOR UPDATE'),
                {'id': str(job_id)}).scalar()
        if token is None or str(token) != str(attempt_token):
            raise AttemptLost('worker attempt was superseded')

    event.listen(db, 'before_flush', check)
    event.listen(db, 'before_commit', check)


def complete_job(db, job_id, success: bool, error: str | None = None,
                 reused: bool = False) -> None:
    """Move a job to READY or FAILED, recording the attempt.

    `attempt_count` increments on every terminal attempt so a retried job shows
    how hard it was to get through; `last_error` keeps the most recent reason.

    `reused` means the worker satisfied the job from content that was already
    processed, without running a single stage. Phase 18's `processing_duration`
    and `queue_wait_time` are both derived from `claimed_at`, so a reuse clears
    it: there was no processing run to measure, and a few-millisecond sample in
    a histogram of multi-second runs is not a measurement of anything. This is
    also why the histograms need no new column to tell the two apart. The
    trade-off, recorded here because it is a real one, is that a reused job also
    stops contributing its genuine queue wait.
    """
    db.execute(text("""
        UPDATE processing_jobs
        SET status = :status,
            attempt_count = attempt_count + 1,
            last_error = :error,
            locked_at = NULL,
            claimed_at = CASE WHEN :reused THEN NULL ELSE claimed_at END,
            updated_at = now()
        WHERE id = :id
    """), {"id": str(job_id),
           "status": JOB_STATUS_READY if success else JOB_STATUS_FAILED,
           "error": None if success else (error or "")[:1000],
           "reused": bool(reused)})
    db.commit()


def max_attempts() -> int:
    """How many times a job may be tried before it is declared FAILED.

    Bounded on purpose: without a cap a job whose content can never be
    processed would be republished forever.
    """
    return _env_int("JOB_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)


# How often the dispatcher looks for stranded work. Five seconds is short
# enough that a save recovers promptly after a queue outage and long enough that
# an idle deployment is not querying the database twice a second.
DEFAULT_DISPATCH_INTERVAL_SECONDS = 5


def dispatch_interval() -> int:
    """Seconds between dispatcher ticks.

    The dispatcher is the only thing that republishes a job a queue outage
    stranded, so how often it looks is an operational knob: a quiet deployment
    can tick every few minutes, a busy one every second. `_env_int` floors it at
    1, because a zero interval would spin the loop against the database as fast
    as it can connect.
    """
    return _env_int("OUTBOX_DISPATCH_INTERVAL_SECONDS",
                    DEFAULT_DISPATCH_INTERVAL_SECONDS)


def record_failure(db, job_id, error: str, attempt_count: int | None = None) -> str:
    """Record a failed attempt. Returns the job's new status.

    Bounded retry with exponential backoff:

    * below the cap the job returns to PENDING with `available_at` pushed
      further out each time, so a struggling queue backs off instead of
      hammering;
    * at the cap the job becomes FAILED and the dispatcher stops picking it up,
      which is what stops infinite retries.

    `attempt_count` is passed in when the caller already knows it, to avoid a
    read-modify-write race between concurrent workers.
    """
    limit = max_attempts()
    if attempt_count is None:
        attempt_count = db.execute(text(
            "SELECT attempt_count FROM processing_jobs WHERE id = :id"),
            {"id": str(job_id)}).scalar() or 0
    attempts = attempt_count + 1

    if attempts >= limit:
        db.execute(text("""
            UPDATE processing_jobs
            SET status = 'FAILED',
                attempt_count = :attempts,
                last_error = :error,
                locked_at = NULL,
                updated_at = now()
            WHERE id = :id
        """), {"id": str(job_id), "attempts": attempts, "error": error[:1000]})
        db.commit()
        log.warning("[outbox] job %s FAILED after %s attempts: %s",
                    job_id, attempts, error)
        return JOB_STATUS_FAILED

    seconds = int(backoff_for(attempts).total_seconds())
    db.execute(text("""
        UPDATE processing_jobs
        SET status = 'PENDING',
            attempt_count = :attempts,
            last_error = :error,
            available_at = now() + make_interval(secs => :delay),
            locked_at = NULL,
            updated_at = now()
        WHERE id = :id
    """), {"id": str(job_id), "attempts": attempts, "error": error[:1000],
           "delay": seconds})
    db.commit()
    return JOB_STATUS_PENDING


def claim_batch(db, limit: int = 50) -> list:
    """Lock and return a batch of due jobs.

    FOR UPDATE SKIP LOCKED lets several dispatchers run at once without two of
    them publishing the same job.
    """
    return db.execute(text("""
        SELECT id, content_id, job_type, attempt_count, status
        FROM processing_jobs
        WHERE (status = 'PENDING'
               OR (status = 'PROCESSING'
                   AND claimed_at IS NULL
                   AND locked_at IS NULL
                   AND updated_at < now() - make_interval(secs => :lock_secs)))
          AND available_at <= now()
          AND (locked_at IS NULL
               OR locked_at < now() - make_interval(secs => :lock_secs))
        ORDER BY available_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    """), {"limit": limit,
           "lock_secs": LOCK_TIMEOUT.total_seconds()}).mappings().all()


def recover_briefs(db, limit: int = 50) -> None:
    """Repair existing versioned saves with stale/missing native provenance."""
    from app.services.brief_v2 import PROMPT_VERSION
    from app.models import JOB_TYPE_PROCESS
    rows = db.execute(text("""
        SELECT i.id, i.content_id FROM items i
        WHERE i.status = 'ready' AND i.deleted_at IS NULL AND i.edited_title IS NULL AND i.edited_summary IS NULL AND i.reprocess_snapshot IS NULL AND NOT i.link_only AND i.brief_v2 <> '{}'::jsonb AND i.content_id IS NOT NULL
          AND (i.brief_v2->>'prompt_version' IS DISTINCT FROM :version
               OR coalesce(i.brief_v2->>'brief_source','') NOT IN ('llm','fallback')
               OR i.brief_v2->>'evidence_level' IS NULL)
          AND NOT EXISTS (SELECT 1 FROM processing_jobs j WHERE j.content_id=i.content_id
                          AND j.job_type=:job_type AND j.status IN ('PENDING','PROCESSING'))
        ORDER BY i.created_at LIMIT :limit FOR UPDATE OF i SKIP LOCKED
    """), {'version': PROMPT_VERSION, 'job_type': JOB_TYPE_PROCESS, 'limit': limit}).all()
    for item_id, content_id in rows:
        db.execute(text('UPDATE items SET needs_retry=true WHERE id=:id'), {'id': item_id})
        record_job(db, content_id, JOB_TYPE_PROCESS)
    db.commit()


WORKER_LOST = "worker lost: its lock expired before the job finished"


def heartbeat_interval() -> int:
    """Seconds between lock refreshes. Far below LOCK_TIMEOUT on purpose."""
    return _env_int("JOB_HEARTBEAT_SECONDS", 30)


class JobHeartbeat:
    """Keeps a running job's `locked_at` fresh from a background thread.

    `locked_at` is stamped once, when the worker claims the job. Without a
    refresh, "the lock is old" cannot tell a worker that is busy with a long
    stage (a transcription, a slow provider) from one that died, so a recovery
    that trusted it would run a second worker on live work. The beat uses its
    own short session because the worker's session is busy inside the pipeline.
    It only touches a job that is still PROCESSING, so once the worker finishes,
    fails or reschedules the job, a late beat changes nothing.
    """

    def __init__(self, job_id, interval: float | None = None, *, attempt_token=None):
        self.job_id = str(job_id)
        self.attempt_token = attempt_token
        self.interval = heartbeat_interval() if interval is None else interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        from app.database import SessionLocal  # looked up late: tests swap the engine

        db = SessionLocal()
        try:
            db.execute(text("UPDATE processing_jobs SET locked_at = now() "
                            "WHERE id = :id AND status = 'PROCESSING' "
                            "AND attempt_token IS NOT DISTINCT FROM CAST(:token AS uuid)"),
                       {"id": self.job_id, "token": str(self.attempt_token) if self.attempt_token else None})
            db.commit()
        except Exception as exc:  # noqa: BLE001 - a missed beat must not kill the job
            db.rollback()
            log.warning("[outbox] heartbeat for job %s failed: %s", self.job_id,
                        type(exc).__name__)
        finally:
            db.close()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.beat()

    def start(self) -> "JobHeartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"job-heartbeat-{self.job_id[:8]}")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def recover_lost_jobs(db, limit: int = 50) -> int:
    """Take back jobs whose worker died. Returns how many were recovered.

    A PROCESSING job whose `locked_at` has stopped being refreshed has no live
    worker. `claim_batch` cannot see it: it reclaims a PROCESSING job only if no
    worker ever claimed it. And even if it could, `_pending_item_ids` skips
    items that are `processing`, so nothing would be published. So the lost run
    is recorded as one failed attempt (which backs off, and ends in FAILED at
    the cap, so a job that kills its worker cannot loop forever), and its
    `processing` items go back to `pending` (or `failed` at the cap). The normal
    dispatch path then republishes them.

    One job per transaction: `record_failure` commits, which would release the
    row locks of any further jobs selected in the same transaction.
    """
    recovered = 0
    for _ in range(limit):
        job = db.execute(text("""
            SELECT id, content_id, attempt_count FROM processing_jobs
            WHERE status = 'PROCESSING' AND locked_at IS NOT NULL
              AND locked_at < now() - make_interval(secs => :lock_secs)
            ORDER BY locked_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """), {"lock_secs": LOCK_TIMEOUT.total_seconds()}).mappings().first()
        if job is None:
            break
        gives_up = (job["attempt_count"] or 0) + 1 >= max_attempts()
        db.execute(text('UPDATE processing_jobs SET attempt_token = NULL WHERE id = :id'),
                   {'id': str(job['id'])})
        db.execute(text("""
            UPDATE items
            SET status = :status,
                failure_reason = CASE WHEN :gives_up THEN :why ELSE failure_reason END
            WHERE content_id = :cid AND status = 'processing' AND deleted_at IS NULL
              AND ((edited_title IS NULL AND edited_summary IS NULL) OR reprocess_snapshot IS NOT NULL)
        """), {"status": "failed" if gives_up else "pending", "gives_up": gives_up,
               "why": WORKER_LOST, "cid": str(job["content_id"])})
        if gives_up:
            from app.services import reprocessing
            for item in db.query(Item).filter(Item.content_id == job['content_id'], Item.reprocess_snapshot.isnot(None), Item.deleted_at.is_(None)):
                reprocessing.fail(item)
        record_failure(db, job["id"], WORKER_LOST,
                       attempt_count=job["attempt_count"])  # commits both
        recovered += 1
        log.warning("[outbox] job %s lost its worker; %s", job["id"],
                    "giving up" if gives_up else "will be retried")
    return recovered


def dispatch_once(db, publisher=None, limit: int = 50) -> dict:
    """Publish one batch of due jobs. Returns a small summary for logging.

    `publisher` is injectable so tests can simulate a dead queue without Redis;
    it defaults to the real Celery publish.
    """
    if publisher is None:
        from app.tasks import process_item

        def publisher(item_id: str) -> None:
            process_item.delay(item_id)

    from app.services.deletion import purge_deleted_saves
    from app.services.retention import purge_expired_guest_staging
    purge_deleted_saves(db, limit)
    purge_expired_guest_staging(db, limit)

    recovered = recover_lost_jobs(db, limit)
    recover_briefs(db, limit)
    stats = {"claimed": 0, "published": 0, "failed": 0, "items": 0, "skipped": 0,
             "recovered": recovered}
    for job in claim_batch(db, limit=limit):
        stats["claimed"] += 1
        # Mark locked first so a concurrent dispatcher skips this job while we
        # publish. Safe to keep even if the publish fails: the failure path
        # clears it.
        db.query(ProcessingJob).filter(
            ProcessingJob.id == job["id"]).update(
            {ProcessingJob.locked_at: text("now()")})
        db.commit()

        item_ids = _pending_item_ids(db, job["content_id"])
        if not item_ids:
            # Preserve the state when there is nothing to publish. A recovered
            # publication must wait another timeout before being checked again.
            changes = {ProcessingJob.locked_at: None}
            if job["status"] == JOB_STATUS_PROCESSING:
                changes[ProcessingJob.updated_at] = text("now()")
            db.query(ProcessingJob).filter(
                ProcessingJob.id == job["id"]).update(changes)
            db.commit()
            stats["skipped"] += 1
            log.info("[outbox] job %s has nothing to publish yet; staying %s",
                     job["id"], job["status"])
            continue
        try:
            for item_id in item_ids:
                publisher(str(item_id))
            stats["items"] += len(item_ids)
            mark_processing(db, job["id"])
            if job["status"] == JOB_STATUS_PROCESSING:
                attempts = (job["attempt_count"] or 0) + 1
                error = f"never claimed by a worker after {attempts} republishes"
                if attempts >= max_attempts():
                    record_failure(db, job["id"], error,
                                   attempt_count=job["attempt_count"])
                    stats["failed"] += 1
                else:
                    db.execute(text("""
                        UPDATE processing_jobs
                        SET attempt_count = :attempts,
                            available_at = now() + make_interval(secs => :delay)
                        WHERE id = :id
                    """), {"id": str(job["id"]), "attempts": attempts,
                           "delay": backoff_for(attempts).total_seconds()})
            db.commit()
            stats["published"] += 1
        except Exception as exc:  # noqa: BLE001 - surviving this is the point
            attempts = (job["attempt_count"] or 0) + 1
            db.rollback()
            record_failure(db, job["id"], f"{type(exc).__name__}: {exc}",
                           attempt_count=job["attempt_count"])
            stats["failed"] += 1
            log.warning("[outbox] publish failed for job %s (attempt %s): %s",
                        job["id"], attempts, exc)
    return stats


def run_forever(db_factory, publisher=None, interval: int = 5,
                limit: int = 50, sleep=time.sleep) -> None:  # pragma: no cover
    """Run the dispatcher until interrupted.

    A plain loop over the EXISTING infrastructure: no new broker and no new
    scheduler. See scripts/dispatch_outbox.py for the entry point.
    """
    while True:
        db = db_factory()
        try:
            stats = dispatch_once(db, publisher=publisher, limit=limit)
            from app.services.readiness import pulse
            pulse('dispatcher')
            if stats["claimed"]:
                log.info("[outbox] %s", stats)
        except Exception as exc:  # noqa: BLE001 - the loop must not die
            log.exception("[outbox] dispatcher tick failed: %s", exc)
        finally:
            db.close()
        sleep(interval)

