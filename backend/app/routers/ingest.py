import logging

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    JOB_STATUS_PENDING, JOB_STATUS_PROCESSING, JOB_TYPE_PROCESS,
    NON_SHARED_VISIBILITIES, VISIBILITY_PUBLIC, VISIBILITY_UNKNOWN,
    ContentAsset, Item, PROCESSING_PENDING, ProcessingJob, UserMemory,
)
from app.schemas import IngestRequest, IngestResponse, SyncBatchRequest, SyncBatchResponse
from app.services import metrics, observability
from app.services.outbox import record_job
from app.services.deletion import restore_save
from app.services.retention import delete_save
from app.tasks import process_item
from app.utils.canonical import canonical_url, source_domain, source_type
from app.utils.dedupe import dedupe_key
from app.utils.text import derive_title, truncate

router = APIRouter(prefix="/api/v1", tags=["ingest"])
log = logging.getLogger("findback.ingest")

RAW_PREVIEW_MAX = 2000
SUMMARY_PLACEHOLDER_MAX = 300


def _record_save(db: Session, user_id, content_id) -> None:
    """Count one save of this content by this user, atomically (Phase 3).

    A single INSERT ... ON CONFLICT statement. The alternative -- SELECT the row,
    add one in Python, UPDATE it -- lets two concurrent saves both read the same
    count and both write count+1, losing a save. Here PostgreSQL serialises the
    increment on the (user_id, content_id) unique index, so N concurrent saves
    always end at N.

    `first_saved_at` is set only on the INSERT branch and never in the DO UPDATE
    clause, which is what keeps the first save's timestamp immutable.

    A row whose asset is unknown (content_id is NULL) cannot be counted: there is
    no key to conflict on. Phase 1's backfill linked every existing item, so this
    only skips genuinely unlinkable rows.
    """
    if content_id is None:
        return
    db.execute(text("""
        INSERT INTO user_memories (user_id, content_id, save_count,
                                   first_saved_at, last_saved_at,
                                   created_at, updated_at)
        VALUES (:uid, :cid, 1, now(), now(), now(), now())
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET save_count = user_memories.save_count + 1,
            last_saved_at = now(),
            updated_at = now()
    """), {"uid": user_id, "cid": content_id})


def _find_reusable_asset(db: Session, user_id, key: str | None, canon: str):
    """The asset this save may join, or None.

    Phase 4 privacy rule -- this is the only place that decides sharing:

    * a PUBLIC asset may be reused by ANY user;
    * a PRIVATE or UNKNOWN asset may be reused only by the user who owns it.

    UNKNOWN is grouped with PRIVATE on purpose: content nobody has classified
    is not shareable, so the safe reading is the private one.
    """
    if key is not None:
        asset = (db.query(ContentAsset)
                   .filter(ContentAsset.dedupe_key == key,
                           or_(
                               ContentAsset.visibility == VISIBILITY_PUBLIC,
                               and_(ContentAsset.visibility.in_(NON_SHARED_VISIBILITIES),
                                    ContentAsset.owner_user_id == user_id),
                           ))
                   .first())
        if asset is not None:
            # Phase 18: a hit here means the save joined existing content.
            metrics.dedupe_hits.inc(labels={"outcome": "key"})
            return asset
    # Fall back to the URL for assets that predate dedupe_key, under the same
    # visibility rule so the fallback cannot become a leak.
    asset = (db.query(ContentAsset)
              .filter(ContentAsset.canonical_url == canon,
                      or_(
                          ContentAsset.visibility == VISIBILITY_PUBLIC,
                          and_(ContentAsset.visibility.in_(NON_SHARED_VISIBILITIES),
                               ContentAsset.owner_user_id == user_id),
                      ))
              .order_by(ContentAsset.created_at)
              .first())
    if asset is not None:
        metrics.dedupe_hits.inc(labels={"outcome": "url"})
    return asset


def _existing_item(db: Session, user_id, canon: str, url: str):
    """This user's existing item for this content, whatever the URL looked like.

    Phase 2 resolves identity through `dedupe_key`, so a second save of the same
    video under a different URL form finds the first save instead of creating a
    duplicate item. Phase 4 restricts the lookup to assets this user is allowed
    to see, so one user can never attach their save to another user's private
    asset.
    """
    key = dedupe_key(url=url)
    asset = _find_reusable_asset(db, user_id, key, canon)
    item = None
    if asset is not None:
        item = (db.query(Item)
                  .filter(Item.user_id == user_id,
                          Item.content_id == asset.id)
                  .first())
    if item is None:
        item = (db.query(Item)
              .filter(Item.user_id == user_id, Item.canonical_url == canon)
              .first())
    if item is not None and item.deleted_at is not None:
        result = restore_save(db, user_id, item.id)
        if result == 'expired':
            delete_save(db, user_id, item.content_id,
                        item_ids=[str(item.id)] if item.content_id is None else None)
            return None
        if result == 'missing':
            return None
    return item


def _reuse_existing_asset(db: Session, user_id, url: str, canon: str,
                         key: str | None):
    """Return (asset, memory, memory_existed).

    Reached only after the insert lost the race to the UNIQUE constraint, which
    is the authority on whether two saves are the same content. Loading the row
    here is what makes concurrent saves converge on one asset.

    `memory_existed` matters for Phase 3: when the memory was already there,
    this save is a *repeat* save and must still be counted, otherwise a save
    lost to a race would silently vanish from save_count.
    """
    asset = _find_reusable_asset(db, user_id, key, canon)
    if asset is None:
        return None
    if asset.dedupe_key is None and key is not None:
        # Claim the key so the next save finds it directly.
        asset.dedupe_key = key
    memory_existed = True
    memory = (db.query(UserMemory)
                .filter(UserMemory.user_id == user_id,
                        UserMemory.content_id == asset.id)
                .first())
    if memory is None:
        memory_existed = False
        candidate = UserMemory(user_id=user_id, content_id=asset.id)
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            memory = candidate
        except IntegrityError:
            # Another save for this user won the race; use its row. The failed
            # insert is already gone (the SAVEPOINT rolled it back), so there
            # is nothing to expunge -- and expunging would detach the row we
            # are about to load.
            memory_existed = True
            memory = (db.query(UserMemory)
                        .filter(UserMemory.user_id == user_id,
                                UserMemory.content_id == asset.id)
                        .one())
    return asset, memory, memory_existed


def _create_asset_and_memory(db: Session, user_id, url: str, canon: str,
                             title_hint, preview):
    """Create the content plus this user's memory of it, deduplicated.

    Phase 2: identical content resolves to ONE ContentAsset that every user
    points at, while each user keeps their own UserMemory row.

    The insert is attempted optimistically and the UNIQUE constraint on
    `dedupe_key` is the arbiter. On conflict the existing asset is loaded and
    reused. That ordering is deliberate: a SELECT-then-INSERT would still race,
    whereas the constraint cannot.

    `user_note`/`user_intent` stay NULL: nothing here collects them, and
    inventing values would put content-derived data in a user-owned field.
    """
    key = dedupe_key(url=url, content=preview or "")
    # Phase 4: reuse BEFORE inserting. A PUBLIC asset no longer collides with a
    # new UNKNOWN one -- the unique indexes are privacy-scoped, so there is no
    # IntegrityError to catch on the shared path. If we only looked on conflict
    # we would create a private duplicate of content that is meant to be
    # shared. The insert below is still kept for the race.
    found = _reuse_existing_asset(db, user_id, url, canon, key)
    if found is not None:
        return found
    asset = ContentAsset(
        canonical_url=canon,
        dedupe_key=key,
        source_type=source_type(url),
        visibility=VISIBILITY_UNKNOWN,
        # Phase 4: everything is created UNKNOWN, which is treated exactly like
        # PRIVATE, so a new asset belongs to the user who saved it and is never
        # shared until something explicitly classifies it PUBLIC.
        owner_user_id=user_id,
        processing_status=PROCESSING_PENDING,
        title=derive_title(title_hint, preview),
        brief=truncate(preview, SUMMARY_PLACEHOLDER_MAX),
    )
    # The asset is added INSIDE the SAVEPOINT: if the insert loses the unique
    # race, the nested block rolls back to the savepoint and the rest of the
    # session stays usable. Adding it first would leave a pending object in the
    # outer transaction that the rollback could not clean up.
    try:
        with db.begin_nested():
            db.add(asset)
            db.flush()
    except IntegrityError:
        found = _reuse_existing_asset(db, user_id, url, canon, key)
        if found is None:
            # The conflict was not ours (e.g. a concurrent delete); let it
            # surface rather than silently pretending the save succeeded.
            raise
        return found
    # The memory has the same race as the asset: two threads for one user can
    # both reach here once the asset insert is resolved, and only one
    # (user_id, content_id) row may exist. Reuse the winner's row.
    memory_existed = True
    memory = (db.query(UserMemory)
                .filter(UserMemory.user_id == user_id,
                        UserMemory.content_id == asset.id)
                .first())
    if memory is None:
        memory_existed = False
        candidate = UserMemory(user_id=user_id, content_id=asset.id)
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            memory = candidate
        except IntegrityError:
            # The SAVEPOINT already rolled the failed insert back, so the row to
            # use is simply the winner's -- and this save is a repeat.
            memory_existed = True
            memory = (db.query(UserMemory)
                        .filter(UserMemory.user_id == user_id,
                                UserMemory.content_id == asset.id)
                        .one())
    return asset, memory, memory_existed


def _new_item(user_id, url: str, canon: str, title_hint, preview,
              asset_id=None) -> Item:
    return Item(
        user_id=user_id,
        url=url,
        canonical_url=canon,
        # derive_title keeps an explicit title_hint winning over the preview
        # head; the old inline expression dropped it whenever preview was None.
        title=derive_title(title_hint, preview),
        source_domain=source_domain(url),
        source_type=source_type(url),
        status="pending",
        raw_preview=truncate(preview, RAW_PREVIEW_MAX),
        # Placeholder summary so an unprocessed item is still findable offline;
        # the worker overwrites it with the real extraction.
        summary=truncate(preview, SUMMARY_PLACEHOLDER_MAX),
        content_id=asset_id,
    )


def _enqueue(db: Session, item_id: str, content_id=None) -> bool:
    """Hand work to Celery. Never process inline: saving must stay under the p95 1.5s budget.

    The previous fallback ran `process_item(...)` synchronously inside the request
    (fetch + LLM + embed), which turned a save into a multi-second call.

    Phase 5: a failed publish is no longer the end of the road. The save was
    already committed together with a ProcessingJob, so the dispatcher will
    publish it once the queue recovers. On success the job is closed out here
    so the dispatcher does not publish the same work a second time.
    """
    try:
        process_item.delay(item_id)
    except Exception as exc:
        log.warning("could not enqueue item %s (%s); dispatcher will retry", item_id, exc)
        return False
    if content_id is not None:
        try:
            job = (db.query(ProcessingJob)
                     .filter(ProcessingJob.content_id == content_id,
                             ProcessingJob.status == JOB_STATUS_PENDING)
                     .first())
            if job is not None:
                job.status = JOB_STATUS_PROCESSING
                job.locked_at = None
                job.updated_at = func.now()
                db.commit()
        except Exception as exc:  # noqa: BLE001
            # Leaving the job pending is harmless: re-publishing is idempotent
            # because the task re-reads the item before doing anything.
            db.rollback()
            log.warning("could not close processing job: %s", exc)
    return True


def _status_for(queued: bool, fallback: str = "pending") -> str:
    return "processing" if queued else fallback


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    from app.services.capacity import intake_limit
    intake_limit(str(user.id), guest=bool((getattr(user, "auth_subject", None) or "guest:").startswith("guest:")))
    canon = canonical_url(req.url)
    # Phase 2: identity is the content, not the URL text. Two URL forms of the
    # same video canonicalize differently but are the same asset, so the
    # existing-item lookup must go through content_id. Matching on canonical_url
    # alone would let the same user create two items for one video.
    existing = _existing_item(db, user.id, canon, req.url)
    if existing:
        metrics.captures_total.inc()
        metrics.dedupe_hits.inc(labels={"outcome": "item"})
        response = _touch(db, existing, canon)
        observability.log_event("ingest.completed", user_id=user.id,
                                content_id=existing.content_id,
                                status="repeated")
        return response

    asset, _memory, memory_existed = _create_asset_and_memory(
        db, user.id, req.url, canon, req.title_hint, req.preview)
    # Phase 3: losing the insert race does not make a save un-counted. If the
    # memory was already there, this request is a repeat save of the same
    # content and must be recorded atomically like any other.
    if memory_existed:
        _record_save(db, user.id, asset.id)
    item = _new_item(user.id, req.url, canon, req.title_hint, req.preview,
                     asset_id=asset.id)
    db.add(item)
    try:
        db.flush()
        # Phase 5: the processing intent is committed in the SAME transaction
        # as the save. If the publish below fails, this row is what the
        # dispatcher will find later.
        job = _record_processing_job(db, asset.id)
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent save of the same canonical URL.
        db.rollback()
        rival = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
        if not rival:
            raise
        response = _touch(db, rival, canon)
        observability.log_event("ingest.completed", user_id=user.id,
                                content_id=rival.content_id, status="raced")
        return response
    db.refresh(item)

    # Phase 18: a miss is the interesting number -- it means new content was
    # created rather than joined, which is what costs money downstream.
    metrics.captures_total.inc()
    if not memory_existed:
        metrics.dedupe_misses.inc(labels={"outcome": "asset"})
    status = _status_for(_enqueue(db, str(item.id), item.content_id))
    observability.log_event("ingest.completed", user_id=user.id,
                            content_id=asset.id, job_id=job.id if job else None,
                            pipeline_version=JOB_TYPE_PROCESS, status=status)
    return IngestResponse(id=item.id, status=status, canonical_url=canon)


def _record_processing_job(db: Session, content_id):
    """Queue durable processing work for this content, inside the caller's tx.

    Returns the job row, so the caller can put its id in the request's log
    line and join the save to the work it queued.
    """
    if content_id is None:
        return None
    try:
        return record_job(db, content_id, JOB_TYPE_PROCESS)
    except Exception as exc:  # noqa: BLE001
        # Never fail a save because the outbox row could not be written.
        log.warning("[ingest] could not record processing job: %s",
                    observability.describe_exc(exc))
        return None


def _touch(db: Session, item: Item, canon: str) -> IngestResponse:
    """A repeat save counts as recency; re-saving a failed item retries it.

    Phase 3: the repeat save is recorded on the user's memory row via one atomic
    statement, so save_count cannot lose an increment under concurrency.
    """
    item.last_seen_at = func.now()
    _record_save(db, item.user_id, item.content_id)
    retry = item.status == "failed"
    if retry:
        item.status = "pending"
        item.failure_reason = None
        # Phase 5: a retry needs a durable job again, in this transaction.
        _record_processing_job(db, item.content_id)
    db.commit()
    if retry:
        return IngestResponse(id=item.id,
                          status=_status_for(_enqueue(db, str(item.id), item.content_id), "failed"),
                          canonical_url=canon, already_exists=True)
    return IngestResponse(id=item.id, status=item.status, canonical_url=canon, already_exists=True)


@router.post("/sync/batch", response_model=SyncBatchResponse)
def sync_batch(req: SyncBatchRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    from app.services.capacity import intake_limit
    if req.items:
        intake_limit(str(user.id), guest=bool((getattr(user, "auth_subject", None) or "guest:").startswith("guest:")), amount=len(req.items))
    mapped, errors = [], []
    for it in req.items:
        try:
            canon = canonical_url(it.url)
            # Same identity rule as /ingest: look the item up through the asset.
            existing = _existing_item(db, user.id, canon, it.url)
            if existing:
                # A replayed offline save is still a save: count it the same
                # way /ingest does, atomically.
                existing.last_seen_at = func.now()
                _record_save(db, user.id, existing.content_id)
                db.commit()
                metrics.captures_total.inc()
                metrics.dedupe_hits.inc(labels={"outcome": "item"})
                mapped.append({"client_id": it.client_id, "id": str(existing.id), "status": existing.status, "canonical_url": canon})
                continue
            asset, _memory, memory_existed = _create_asset_and_memory(
                db, user.id, it.url, canon, it.title_hint, it.preview)
            if memory_existed:
                _record_save(db, user.id, asset.id)
            item = _new_item(user.id, it.url, canon, it.title_hint, it.preview,
                             asset_id=asset.id)
            db.add(item)
            db.flush()
            # Same transaction as the save, for the same reason as /ingest.
            _record_processing_job(db, asset.id)
            db.commit()
            metrics.captures_total.inc()
            if not memory_existed:
                metrics.dedupe_misses.inc(labels={"outcome": "asset"})
            mapped.append({"client_id": it.client_id, "id": str(item.id),
                           "status": _status_for(_enqueue(db, str(item.id), item.content_id)),
                           "canonical_url": canon})
        except IntegrityError:
            db.rollback()
            rival = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
            if not rival:
                errors.append({"client_id": it.client_id, "error": "duplicate insert conflict"})
                continue
            mapped.append({"client_id": it.client_id, "id": str(rival.id), "status": rival.status, "canonical_url": canon})
        except Exception as e:
            # Without this rollback one poison row aborts every later item in the batch.
            db.rollback()
            # The batch's `errors` list is returned to the CLIENT, so the reason
            # is reported here by type and length only: a provider or database
            # exception routinely quotes the row it failed on, which carries the
            # user's own content back out through the response.
            observability.log_event(
                "ingest.batch_item_failed", level=logging.WARNING,
                user_id=user.id, status="failed")
            errors.append({"client_id": it.client_id,
                           "error": observability.describe_exc(e)})
    return SyncBatchResponse(mapped=mapped, errors=errors)

