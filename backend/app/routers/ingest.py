import logging

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.auth import get_current_user
from app.database import get_db
from app.models import Item
from app.schemas import IngestRequest, IngestResponse, SyncBatchRequest, SyncBatchResponse
from app.tasks import process_item
from app.utils.canonical import canonical_url, source_domain, source_type
from app.utils.text import derive_title, truncate

router = APIRouter(prefix="/api/v1", tags=["ingest"])
log = logging.getLogger("findback.ingest")

RAW_PREVIEW_MAX = 2000
SUMMARY_PLACEHOLDER_MAX = 300


def _new_item(user_id, url: str, canon: str, title_hint, preview) -> Item:
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
    )


def _enqueue(item_id: str) -> bool:
    """Hand work to Celery. Never process inline: saving must stay under the p95 1.5s budget.

    The previous fallback ran `process_item(...)` synchronously inside the request
    (fetch + LLM + embed), which turned a save into a multi-second call. A failed
    publish now just leaves the item `pending`, to be picked up by a re-save or a
    requeue job.
    """
    try:
        process_item.delay(item_id)
        return True
    except Exception as exc:
        log.warning("could not enqueue item %s (%s); left pending", item_id, exc)
        return False


def _status_for(queued: bool, fallback: str = "pending") -> str:
    return "processing" if queued else fallback


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    canon = canonical_url(req.url)
    existing = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
    if existing:
        return _touch(db, existing, canon)

    item = _new_item(user.id, req.url, canon, req.title_hint, req.preview)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent save of the same canonical URL.
        db.rollback()
        rival = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
        if not rival:
            raise
        return _touch(db, rival, canon)
    db.refresh(item)

    return IngestResponse(id=item.id, status=_status_for(_enqueue(str(item.id))), canonical_url=canon)


def _touch(db: Session, item: Item, canon: str) -> IngestResponse:
    """A repeat save counts as recency; re-saving a failed item retries it."""
    item.last_seen_at = func.now()
    retry = item.status == "failed"
    if retry:
        item.status = "pending"
        item.failure_reason = None
    db.commit()
    if retry:
        return IngestResponse(id=item.id, status=_status_for(_enqueue(str(item.id)), "failed"), canonical_url=canon)
    return IngestResponse(id=item.id, status=item.status, canonical_url=canon)


@router.post("/sync/batch", response_model=SyncBatchResponse)
def sync_batch(req: SyncBatchRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    mapped, errors = [], []
    for it in req.items:
        try:
            canon = canonical_url(it.url)
            existing = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
            if existing:
                existing.last_seen_at = func.now()
                db.commit()
                mapped.append({"client_id": it.client_id, "id": str(existing.id), "status": existing.status, "canonical_url": canon})
                continue
            item = _new_item(user.id, it.url, canon, it.title_hint, it.preview)
            db.add(item)
            db.commit()
            mapped.append({"client_id": it.client_id, "id": str(item.id), "status": _status_for(_enqueue(str(item.id))), "canonical_url": canon})
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
            errors.append({"client_id": it.client_id, "error": str(e)})
    return SyncBatchResponse(mapped=mapped, errors=errors)

