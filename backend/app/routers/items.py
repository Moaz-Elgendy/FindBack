import hashlib
import json
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, or_, func
from pydantic import AwareDatetime
from sqlalchemy.orm import Session, defer

from app.database import get_db
from app.auth import get_current_user
from app.models import Item, ProcessingJob, JOB_ACTIVE_STATUSES, JOB_TYPE_PROCESS
from app.schemas import ItemDetail, ItemEdit, SummarizeAgainRequest
from app.services import reprocessing
from app.services import retention
from app.services.deletion import soft_delete_save, restore_save
from app.categories import Category
from app.services.intelligence import query_for, read_filters
from app.services.outbox import record_job, _pending_item_ids
from app.routers.ingest import _enqueue, _create_asset_and_memory

router = APIRouter(prefix="/api/v1/items", tags=["items"])

# Columns no response here ever shows. The 1536-float embedding alone is about
# 12 KB of text per row, and a page of 20 used to pull all of it (plus the raw
# page text and every chunk) across the wire from Postgres for nothing.
_NOT_NEEDED = (defer(Item.embedding), defer(Item.raw_text),
               defer(Item.normalized_text), defer(Item.chunk_texts),
               defer(Item.chunk_timestamps), defer(Item.search_text))

# What only the detail screen reads. The list endpoint leaves them out: a
# transcript can be thousands of segments, and the list shows none of it.
# GET /items/{id} still returns everything.
_LIST_EXCLUDE = {"transcript", "ocr_text", "evidence_used", "processing_metadata"}

MAX_PAGE = 100

# `created_at` + `id`, so rows saved in the same instant have a total order and a
# cursor survives its own row being deleted. Z-suffixed so no `+` has to survive
# a query string.
_CURSOR_TIME = "%Y-%m-%dT%H:%M:%S.%fZ"


def _encode_cursor(item: Item) -> str:
    stamp = item.created_at.astimezone(timezone.utc).strftime(_CURSOR_TIME)
    return f"{stamp}|{item.id}"


def _decode_cursor(db: Session, user, cursor: str) -> tuple[datetime, UUID]:
    try:
        if "|" in cursor:
            stamp, _, raw_id = cursor.partition("|")
            return (datetime.strptime(stamp, _CURSOR_TIME)
                    .replace(tzinfo=timezone.utc), UUID(raw_id))
        # A bare item id: the cursor format before this one. Still accepted so a
        # client mid-scroll across an upgrade is not stranded.
        row = (db.query(Item.created_at, Item.id)
                 .filter(Item.id == UUID(cursor), Item.user_id == user.id).first())
    except ValueError:
        raise HTTPException(422, "invalid cursor") from None
    if row is None:
        raise HTTPException(422, "cursor no longer valid")
    return row.created_at, row.id


@router.get("", response_model=dict)
def list_items(request: Request = None, response: Response = None, limit: int = Query(20, ge=1, le=MAX_PAGE), cursor: str | None = None,
               category: Category = None, saved_after: AwareDatetime | None = None, intelligence: dict = Depends(read_filters),
               db: Session = Depends(get_db), user = Depends(get_current_user)):
    q = query_for(db, user.id, intelligence if isinstance(intelligence, dict) else {}).options(*_NOT_NEEDED)
    if saved_after is not None:
        q = q.filter(Item.created_at >= saved_after)
    if category is not None:
        q = q.filter(Item.category == category.value)
    if cursor:
        at, last_id = _decode_cursor(db, user, cursor)
        q = q.filter(or_(Item.created_at < at,
                         and_(Item.created_at == at, Item.id < last_id)))
    rows = q.order_by(Item.created_at.desc(), Item.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    payload = {"items": [ItemDetail.model_validate(i).model_dump(exclude=_LIST_EXCLUDE) for i in items],
               "next_cursor": _encode_cursor(items[-1]) if has_more else None}
    encoded = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(',', ':'))
    etag = '"' + hashlib.sha256((str(user.id) + encoded).encode()).hexdigest() + '"'
    headers = {'ETag': etag, 'Cache-Control': 'private, no-cache', 'Vary': 'Authorization'}
    if request is not None:
        tags = [tag.strip().removeprefix('W/') for tag in request.headers.get('if-none-match', '').split(',')]
        if etag in tags or '*' in tags:
            return Response(status_code=304, headers=headers)
    if response is not None:
        response.headers.update(headers)
    return payload

@router.get("/{item_id}", response_model=ItemDetail)
def get_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item = (db.query(Item).options(*_NOT_NEEDED)
              .filter(Item.id == item_id, Item.user_id == user.id, Item.deleted_at.is_(None)).first())
    if not item: raise HTTPException(404, "not found")
    return item


def _action_item(db, user_id, item_id):
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user_id,
                                 Item.deleted_at.is_(None)).first()
    if item is None:
        raise HTTPException(404, "not found")
    # Workers fence their writes on the job before updating the item.
    job = (db.query(ProcessingJob).filter(ProcessingJob.content_id == item.content_id,
                                        ProcessingJob.job_type == JOB_TYPE_PROCESS,
                                        ProcessingJob.status.in_(JOB_ACTIVE_STATUSES))
           .with_for_update().first()) if item.content_id else None
    item = (db.query(Item).filter(Item.id == item_id, Item.user_id == user_id,
                                 Item.deleted_at.is_(None))
            .populate_existing().with_for_update().first())
    if item is None:
        raise HTTPException(404, "not found")
    return item, job


@router.patch("/{item_id}", response_model=ItemDetail)
def edit_item(item_id: UUID, edit: ItemEdit, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item, _ = _action_item(db, user.id, item_id)
    if 'title' in edit.model_fields_set:
        item.edited_title = edit.title
    if 'summary' in edit.model_fields_set:
        item.edited_summary = edit.summary
    db.commit()
    return item


@router.post("/{item_id}/summarize-again", response_model=ItemDetail, status_code=202)
def summarize_again(item_id: UUID, request: SummarizeAgainRequest = SummarizeAgainRequest(), db: Session = Depends(get_db), user = Depends(get_current_user)):
    item, job = _action_item(db, user.id, item_id)
    if reprocessing.edited(item) and not request.replace_edits:
        raise HTTPException(409, "Replace your edits with a new summary?")
    if item.reprocess_snapshot:
        return item
    if item.status in ('pending', 'processing') or (job and job.attempt_token is not None):
        raise HTTPException(409, "This memory is being read. Try again when it finishes")
    from app import env
    now = datetime.now(timezone.utc)
    today = now.date()
    count = item.regeneration_count if item.regeneration_day == today else 0
    limit = max(1, env.get_int('SUMMARIZE_AGAIN_DAILY_LIMIT', 3))
    if count >= limit:
        midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        raise HTTPException(429, 'Daily summary limit reached. Try again tomorrow.',
                            headers={'Retry-After': str(max(1, int((midnight - now).total_seconds()) + 1))})
    item.regeneration_day, item.regeneration_count = today, count + 1
    reprocessing.detach(db, item)
    item.reprocess_snapshot = reprocessing.snapshot(item, request.replace_edits)
    item.reprocess_failure = None
    item.link_only = False
    item.status = 'ready'
    item.failure_reason = None
    item.needs_retry = False
    job = record_job(db, item.content_id, JOB_TYPE_PROCESS)
    job.last_stage = None
    job.available_at = func.now()
    db.commit()
    _enqueue(db, str(item.id), item.content_id)
    return item


@router.post("/{item_id}/retry", response_model=ItemDetail, status_code=202)
def retry_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item, job = _action_item(db, user.id, item_id)
    if reprocessing.edited(item):
        raise HTTPException(409, "Edited memories require Summarize again confirmation")
    if item.link_only:
        raise HTTPException(409, "This memory is saved as a link only")
    if item.status in ('pending', 'processing'):
        return item
    if item.status != 'failed' and not item.needs_retry:
        raise HTTPException(409, "This memory does not need a retry")
    if job and job.attempt_token is not None:
        raise HTTPException(409, "This memory is being read. Try again when it finishes")
    if item.content_id is None:
        asset, _, _ = _create_asset_and_memory(db, user.id, item.url, item.canonical_url,
                                               item.title, item.raw_preview)
        item.content_id = asset.id
    job = record_job(db, item.content_id, JOB_TYPE_PROCESS)
    job.available_at = func.now()
    item.status = 'pending'
    item.failure_reason = None
    item.needs_retry = False
    db.commit()
    _enqueue(db, str(item.id), item.content_id)
    return item


@router.post("/{item_id}/keep-link", response_model=ItemDetail)
def keep_link(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item, job = _action_item(db, user.id, item_id)
    if item.link_only:
        return item
    if item.status != 'failed' and not item.needs_retry:
        raise HTTPException(409, "This memory does not need a fallback")
    if job and job.attempt_token is not None:
        raise HTTPException(409, "This memory is being read. Try again when it finishes")
    item.link_only = True
    item.status = 'ready'
    item.needs_retry = False
    item.failure_reason = None
    db.flush()
    if job and not _pending_item_ids(db, item.content_id):
        job.status = 'READY'
        job.locked_at = None
        job.attempt_token = None
    db.commit()
    return item

@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user),
                undoable: bool = False):
    if undoable:
        if not soft_delete_save(db, user.id, item_id):
            raise HTTPException(404, "not found")
        return None
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user.id).first()
    if not item: raise HTTPException(404, "not found")
    if item.content_id is None:
        # A row with no asset (created before Phase 1, or unlinked): there is no
        # memory to remove, so the row itself is the whole save.
        db.delete(item)
        db.commit()
        return None
    # The save is the item AND this user's memory of the content. Deleting only
    # the item would leave the note, the intent and the save history behind, and
    # a later save of the same link would bring them back. delete_save removes
    # the item(s) first and then the memory, in one transaction.
    retention.delete_save(db, user.id, item.content_id)
    return None


@router.post("/{item_id}/restore", status_code=204)
def restore_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    result = restore_save(db, user.id, item_id)
    if result == 'missing':
        raise HTTPException(404, "not found")
    if result == 'expired':
        raise HTTPException(409, "Undo window expired")
    return None


@router.post("/{item_id}/open", status_code=204)
def open_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    # Own and non-deleted only; anything else is 404, like every action here.
    item, _ = _action_item(db, user.id, item_id)
    # `_action_item` locked the row, so a racing second open waits and then
    # re-reads the committed value; the NULL check means the first open is the
    # only write. Repeat calls return the same 204 and change nothing.
    if item.first_opened_at is None:
        item.first_opened_at = func.now()
        db.commit()
    return None
