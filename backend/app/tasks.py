from sqlalchemy import text

from app.celery_app import celery
from app.database import SessionLocal
from app.models import (
    JOB_STATUS_FAILED, JOB_STATUS_READY, JOB_TYPE_PROCESS, VISIBILITY_PUBLIC,
    Chunk, ContentAsset, Item, ProcessingJob,
)
from app.services import embedder, observability, retention
from app.services.outbox import (AttemptLost, JobHeartbeat, claim_job, complete_job,
                                 fence_attempt, record_failure)
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


def _copy_to_asset(db, item, mem, fetched, pipeline_version=None):
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
    # H3: record which pipeline produced this asset, so a later save of the same
    # content can tell "already done at this version" from "never done". Nothing
    # is backfilled for existing rows: a NULL stamp means "process once more,
    # then stamp", which is the only safe reading of an asset that predates the
    # stamp.
    asset.pipeline_version = pipeline_version or JOB_TYPE_PROCESS
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


def _reuse_source(db, item, job):
    """An already-processed item whose derived data this save may reuse, or None.

    H3 step 1. Reuse is an optimisation, so every doubt falls through to the
    full pipeline: a wrong reuse would be a wrong or missing memory, and paying
    for one more fetch is always better than that. The conditions, all required:

    * `item.content_id` is set and the user may reach that content at all --
      PUBLIC, or their own copy. This mirrors `_find_reusable_asset` in
      `app/routers/ingest.py`, which is the only code that decides what a save
      is allowed to attach to; the worker repeats the rule rather than trusting
      that every writer went through it.
    * the asset is READY **and stamped with the job type being run**, so a later
      pipeline version re-processes instead of reusing an older shape;
    * an embedding model is configured, and a source item exists that is ready,
      has vectors, and was embedded with that same model. A vector from another
      model is not comparable with a query vector from this one.
    * the source has a complete brief and at least one chunk row, because search
      reaches content through `chunks`.

    Nothing on `user_memories` is read here. The note and the intent belong to
    one user and cannot be reused by another, so they are never consulted.
    """
    if item.content_id is None or job is None:
        return None
    asset = (db.query(ContentAsset)
               .filter(ContentAsset.id == item.content_id).first())
    if asset is None or asset.processing_status != JOB_STATUS_READY:
        return None
    if not (asset.visibility == VISIBILITY_PUBLIC
            or asset.owner_user_id == item.user_id):
        return None
    if asset.pipeline_version != job.job_type:
        return None

    model = embedder.embedding_model_name()
    if not model:
        # No embedding provider configured: there are no vectors to hand over.
        return None
    source = (db.query(Item)
                .filter(Item.content_id == item.content_id,
                        Item.id != item.id,
                        Item.status == "ready",
                        Item.embedding.isnot(None),
                        Item.embedding_model == model)
                .order_by(Item.created_at.asc())
                .first())
    if source is None:
        return None
    if not (source.fetch_metadata or {}).get("brief"):
        return None
    if source.needs_retry:
        return None
    from app.services.brief_v2 import PROMPT_VERSION
    if not source.brief_v2 or source.brief_v2.get("brief_source") == "fallback" or (source.processing_metadata or {}).get("prompt_version") != PROMPT_VERSION:
        return None
    # A v2 job must not reuse a weaker video artifact and suppress acquisition.
    if (source.evidence_bundle or {}) and source.evidence_bundle.get("evidence_level") != "full_transcript":
        from app.services.fetcher import video_source
        if video_source(item.url or ""): return None
    if db.execute(text("SELECT 1 FROM chunks WHERE item_id = :i LIMIT 1"),
                  {"i": str(source.id)}).first() is None:
        return None
    return source


def _reuse_derived_data(db, item, source) -> int:
    """Copy the shared understanding of the content onto this user's item.

    Only what describes the CONTENT moves, and nothing arrives from the source's
    user-owned side:

    * `user_note` and `user_intent` live on `user_memories` and are never read,
      so they cannot travel to another user;
    * the source's `search_text` is deliberately NOT copied. It is derived from
      the source's own item row, and copying one user's derived text onto
      another's is the mistake this function exists to avoid. It is recomputed
      from the brief below with the same pure function the pipeline uses, so a
      reused item ends up byte-for-byte what the pipeline would have produced;
    * the raw page text is not copied either: the retention policy drops it, and
      a reuse needs no copy of it.

    Returns the number of chunk rows given to this item.
    """
    from app.services import pipeline

    item.fetch_metadata = dict(
        item.fetch_metadata or {},
        brief=dict((source.fetch_metadata or {}).get("brief") or {}))
    item.evidence_bundle = dict(source.evidence_bundle or {})
    item.brief_v2 = dict(source.brief_v2 or {})
    item.processing_metadata = dict(source.processing_metadata or {}, cache_hit=True)
    item.needs_retry = False
    item.title_clean = source.title_clean or item.title
    item.entities = dict(source.entities or {})
    item.tags = list(source.tags or [])
    item.intent = source.intent
    item.embedding = source.embedding
    item.embedding_model = source.embedding_model
    item.source_type = source.source_type or item.source_type
    if source.thumbnail_url and not item.thumbnail_url:
        item.thumbnail_url = source.thumbnail_url

    # BRIEF is the pure, local stage: it turns the brief into summary,
    # key_points, category and the Phase 12 search document. Running it keeps
    # the reused item identical to a processed one without a model call.
    asyncio.run(pipeline.stage_brief(item))

    # Chunks are keyed by item, so this item needs its own rows. The copy is an
    # INSERT ... SELECT, so the vectors never leave Postgres. Delete first for
    # the same reason stage_embed does: a retried reuse must not duplicate rows.
    db.execute(text("DELETE FROM chunks WHERE item_id = :i"),
               {"i": str(item.id)})
    result = db.execute(text("""
        INSERT INTO chunks (id, item_id, chunk_idx, chunk_text,
                            start_timestamp, start_seconds, embedding)
        SELECT gen_random_uuid(), :target, chunk_idx, chunk_text,
               start_timestamp, start_seconds, embedding
          FROM chunks
         WHERE item_id = :source
    """), {"target": str(item.id), "source": str(source.id)})
    return result.rowcount or 0


@celery.task(name="process_item", bind=True, max_retries=2)
def process_item(self, item_id: str):
    db = SessionLocal()
    job = None
    heartbeat = None
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
            if item.needs_retry and job.available_at > datetime.datetime.now(datetime.timezone.utc):
                return {"id": str(item.id), "status": item.status, "skipped": "improvement not due"}
            if not claim_job(db, job.id):
                # Another worker owns this content right now.
                return {"id": str(item.id), "status": "skipped",
                        "reason": "claimed by another worker"}
        else:
            job = ProcessingJob(content_id=item.content_id,
                                job_type=JOB_TYPE_PROCESS)
            db.add(job)
            db.commit()
            if not claim_job(db, job.id):
                return {"id": str(item.id), "status": "skipped"}

        # From here until the worker is done the lock is refreshed, so the
        # dispatcher can tell a busy worker from a dead one (see JobHeartbeat).
        token = job.attempt_token
        fence_attempt(db, job.id, token)
        heartbeat = JobHeartbeat(job.id, attempt_token=token).start()

        if not (item.summary and item.needs_retry):
            item.status = "processing"
        db.commit()

        # H3 step 1: content already processed at this pipeline version is
        # handed to the new save instead of being fetched, understood and
        # embedded a second time. The job still goes PENDING -> PROCESSING ->
        # READY, so the outbox and the state machine behave exactly as before;
        # only the stages are skipped.
        source = _reuse_source(db, item, job)
        if source is not None:
            chunks = _reuse_derived_data(db, item, source)
            item.status = "ready"
            item.failure_reason = None
            item.processed_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
            complete_job(db, job.id, success=True, reused=True)
            # A distinct event, from the existing field vocabulary, so a reuse
            # is visible in the log without inventing a stage that ran.
            observability.log_event(
                "job.reused", content_id=item.content_id, job_id=job.id,
                pipeline_version=job.job_type, stage="REUSED", status="ready",
                duration_ms=int(round((time.monotonic() - began) * 1000)))
            log.info("[task] reused processed content for item %s (%d chunks)",
                     item.id, chunks)
            return {"id": str(item.id), "status": "ready", "reused": True}

        # Phase 8: the pipeline is a sequence of explicit stages. Each one
        # commits its own output and records `last_stage`, so a retry resumes
        # at the stage after the last one that succeeded.
        progress = _StageProgress(db, item, job)
        asyncio.run(progress.run_all())

        item.status = "ready"
        item.failure_reason = None
        item.processed_at = datetime.datetime.now(datetime.timezone.utc)
        _copy_to_asset(db, item, None, None, pipeline_version=job.job_type)
        if job is not None:
            from app.services import brief_retry
            if not brief_retry.schedule(db, item, job):
                complete_job(db, job.id, success=True)
        else:
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
        observability.log_event(
            "job.completed", content_id=item.content_id, job_id=job.id if job else None,
            pipeline_version=JOB_TYPE_PROCESS, stage="READY", status="ready",
            duration_ms=int(round((time.monotonic() - began) * 1000)))
        return {"id": str(item.id), "status": "ready"}
    except AttemptLost:
        db.rollback()
        return {"id": item_id, "status": "skipped", "reason": "attempt superseded"}
    except Exception as e:
        if heartbeat is not None:
            heartbeat.stop()  # before the job is rescheduled, not after
        db.rollback()
        content_id = None
        try:
            item = db.query(Item).filter(Item.id == item_id).first()
            if item:
                content_id = item.content_id
                item.status = "ready" if item.summary and item.needs_retry else "failed"
                item.failure_reason = str(e)[:1000]
                db.commit()
        except AttemptLost:
            db.rollback()
            return {"id": item_id, "status": "skipped", "reason": "attempt superseded"}
        except Exception: pass
        # Phase 6: a retryable failure keeps the job active, counts the attempt
        # and records why, instead of silently disappearing.
        if job is not None:
            try:
                record_failure(db, job.id, f"{type(e).__name__}: {e}")
            except AttemptLost:
                db.rollback()
                return {"id": item_id, "status": "skipped", "reason": "attempt superseded"}
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
        if heartbeat is not None:
            heartbeat.stop()
        db.close()
