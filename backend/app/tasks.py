from app.celery_app import celery
from app.database import SessionLocal
from app.models import (
    JOB_STATUS_FAILED, JOB_STATUS_READY, JOB_TYPE_PROCESS, Chunk, ContentAsset,
    Item, ProcessingJob,
)
from app.services.fetcher import fetch_content
from app.services.extractor import extract_memory
from app.services.embedder import memory_string, embed_many, chunk_text, embedding_model_name
from app.services.limits import AI_LIMIT, EMBEDDING_LIMIT, FETCH_LIMIT
from app.services.outbox import claim_job, complete_job, record_failure
from app.services import observability, retention
from app.services.storage import store_raw_snapshot
import datetime
import asyncio
import logging
import time

log = logging.getLogger("findback.task")


class _StageProgress:
    """Runs the unfinished pipeline stages and records how far it got.

    After every successful stage the job's `last_stage` is committed, so a
    crash or failure mid-pipeline leaves a durable record of where to resume.
    The commit happens per stage precisely so that a later failure does not
    roll back the earlier stages' work.
    """

    def __init__(self, db, item, job):
        self.db = db
        self.item = item
        self.job = job

    def _last_stage(self) -> str | None:
        if self.job is None:
            return None
        return self.job.last_stage

    def _record(self, stage: str) -> None:
        if self.job is not None:
            self.job.last_stage = stage
        self.db.commit()

    async def run_all(self) -> str:
        """Run the stages, reporting each one (Phase 18).

        The per-stage event is what makes a slow pipeline diagnosable: a log
        that only says "this job took four minutes" cannot say which of the six
        stages spent it. `stage` is a slug from `pipeline.STAGE_ORDER`, so the
        field cannot carry content.
        """
        from app.services import pipeline

        start = pipeline.resume_index(self._last_stage())
        # `resume_index` returns len(STAGE_ORDER) when the recorded stage was the
        # last one, i.e. there is nothing left to run. Indexing STAGE_ORDER with
        # that would raise, so the name is taken only when a stage actually
        # exists.
        observability.log_event(
            "pipeline.started", content_id=self.item.content_id,
            job_id=self.job.id if self.job else None,
            pipeline_version=self.job.job_type if self.job else None,
            stage=pipeline.STAGE_ORDER[start]
            if start < len(pipeline.STAGE_ORDER) else "COMPLETE",
            status="resumed" if start > 0 else "fresh")
        for index in range(start, len(pipeline.STAGE_ORDER)):
            stage = pipeline.STAGE_ORDER[index]
            handler = pipeline._STAGES[stage]
            began = time.monotonic()
            if stage == pipeline.STAGE_EMBED:
                await handler(self.item, self.db)
            else:
                await handler(self.item)
            self._record(stage)
            observability.log_event(
                "pipeline.stage_completed", content_id=self.item.content_id,
                job_id=self.job.id if self.job else None,
                pipeline_version=self.job.job_type if self.job else None,
                stage=stage, status="ok",
                duration_ms=int(round((time.monotonic() - began) * 1000)))
        return pipeline.STAGE_ORDER[-1]


def _copy_to_asset(db, item, mem, fetched):
    """Mirror the processed item onto its ContentAsset (Phase 6).

    A pure overwrite, so running it twice produces the same row: idempotent by
    construction rather than by a "did we already?" check that could race.
    """
    if item.content_id is None:
        return
    asset = db.query(ContentAsset).filter(ContentAsset.id == item.content_id).first()
    if asset is None:
        return
    asset.processing_status = JOB_STATUS_READY
    asset.processing_error = None
    asset.title = item.title_clean or item.title
    asset.brief = item.summary
    # The whole Brief lands here too (Phase 9). structured_data is free-form
    # JSONB, so a new content type or brief field needs no migration. The
    # legacy keys stay for the Phase 1 backfill contract.
    stored_brief = (item.fetch_metadata or {}).get("brief")
    structured = {"key_points": item.key_points, "category": item.category}
    if stored_brief:
        structured["brief"] = stored_brief
    asset.structured_data = structured
    asset.entities = item.entities
    asset.topics = item.tags
    asset.intent = item.intent
    asset.raw_content = item.raw_s3_key
    asset.source_type = item.source_type
    asset.processed_at = item.processed_at
    asset.updated_at = datetime.datetime.now(datetime.timezone.utc)


@celery.task(name="process_item", bind=True, max_retries=2)
def process_item(self, item_id: str):
    db = SessionLocal()
    job = None
    # Phase 18. `started` is taken before anything can fail, so the duration is
    # reported for failures too -- a pipeline that always dies at minute four is
    # the case worth seeing. The counters are NOT incremented here: success and
    # failure totals are derived from `processing_jobs` at scrape time, because
    # this runs in a worker process whose memory the API's /metrics cannot see.
    began = time.monotonic()
    try:
        item = db.query(Item).filter(Item.id == item_id).first()
        if not item: return {"error": "not found"}

        # Phase 6 idempotency. Two guards, in this order:
        #   1. Already finished -> do nothing at all. Re-running a completed job
        #      must not re-fetch, re-extract, or re-embed anything.
        #   2. Atomically claim the job. Only the worker whose conditional
        #      UPDATE matched a PENDING job may process, so two workers can
        #      never work on the same content at once.
        job = (db.query(ProcessingJob)
                 .filter(ProcessingJob.content_id == item.content_id,
                         ProcessingJob.job_type == JOB_TYPE_PROCESS)
                 .order_by(ProcessingJob.created_at.desc())
                 .first())
        if job is not None and job.status in (JOB_STATUS_READY, JOB_STATUS_FAILED):
            # Terminal: re-running must be a no-op, not a second processing.
            observability.log_event(
                "job.skipped", content_id=item.content_id, job_id=job.id,
                pipeline_version=job.job_type, status="already_finished")
            return {"id": str(item.id), "status": item.status,
                    "skipped": "job already finished"}
        if job is not None:
            if not claim_job(db, job.id):
                # Another worker owns this content right now.
                return {"id": str(item.id), "status": "skipped",
                        "reason": "claimed by another worker"}
        else:
            job = ProcessingJob(content_id=item.content_id,
                                job_type=JOB_TYPE_PROCESS)
            db.add(job)
            db.commit()

        item.status = "processing"
        db.commit()
        # Phase 8: the pipeline is a sequence of explicit stages. Each one
        # commits its own output and records `last_stage`, so a retry resumes
        # at the stage after the last one that succeeded.
        progress = _StageProgress(db, item, job)
        asyncio.run(progress.run_all())

        item.status = "ready"
        item.failure_reason = None
        item.processed_at = datetime.datetime.now(datetime.timezone.utc)
        _copy_to_asset(db, item, None, None)
        db.commit()
        # Phase 14: the raw fetched text has done its job once the stages are
        # done. The brief, chunks and vectors stay -- they are what makes the
        # memory findable -- but the copy of the page itself does not.
        try:
            retention.purge_raw_text(db, [item.id])
        except Exception as exc:  # retention must never fail a good ingest
            db.rollback()
            log.warning("[task] raw-text purge skipped: %s",
                        observability.describe_exc(exc))
        if job is not None:
            complete_job(db, job.id, success=True)
        observability.log_event(
            "job.completed", content_id=item.content_id, job_id=job.id if job else None,
            pipeline_version=JOB_TYPE_PROCESS, stage="READY", status="ready",
            duration_ms=int(round((time.monotonic() - began) * 1000)))
        return {"id": str(item.id), "status": "ready"}
    except Exception as e:
        db.rollback()
        content_id = None
        try:
            item = db.query(Item).filter(Item.id == item_id).first()
            if item:
                content_id = item.content_id
                item.status = "failed"
                item.failure_reason = str(e)[:1000]
                db.commit()
        except Exception: pass
        # Phase 6: a retryable failure keeps the job active, counts the attempt
        # and records why, instead of silently disappearing.
        if job is not None:
            try:
                record_failure(db, job.id, f"{type(e).__name__}: {e}")
            except Exception:  # noqa: BLE001
                db.rollback()
        # Phase 18: the reason goes to the log BY TYPE AND LENGTH only, while the
        # job row keeps the full text for an operator reading the database. A
        # provider exception quotes the request that failed, which is the user's
        # own content, so it must not reach a log that is shipped and grepped.
        observability.log_event(
            "job.failed", level=logging.WARNING, content_id=content_id,
            job_id=job.id if job else None,
            pipeline_version=JOB_TYPE_PROCESS,
            stage=job.last_stage if job else None, status="failed",
            duration_ms=int(round((time.monotonic() - began) * 1000)))
        log.warning("[task] process_item failed: %s",
                    observability.describe_exc(e))
        raise self.retry(exc=e, countdown=10)
    finally:
        db.close()
