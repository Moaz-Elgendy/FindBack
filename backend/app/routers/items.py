from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, defer

from app.database import get_db
from app.auth import get_current_user
from app.models import Item
from app.schemas import ItemDetail
from app.services import retention
from app.categories import Category
from app.services.intelligence import query_for, read_filters

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
def list_items(limit: int = Query(20, ge=1, le=MAX_PAGE), cursor: str | None = None,
               category: Category = None, intelligence: dict = Depends(read_filters),
               db: Session = Depends(get_db), user = Depends(get_current_user)):
    q = query_for(db, user.id, intelligence if isinstance(intelligence, dict) else {}).options(*_NOT_NEEDED)
    if category is not None:
        q = q.filter(Item.category == category.value)
    if cursor:
        at, last_id = _decode_cursor(db, user, cursor)
        q = q.filter(or_(Item.created_at < at,
                         and_(Item.created_at == at, Item.id < last_id)))
    rows = q.order_by(Item.created_at.desc(), Item.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return {"items": [ItemDetail.model_validate(i).model_dump(exclude=_LIST_EXCLUDE)
                      for i in items],
            "next_cursor": _encode_cursor(items[-1]) if has_more else None}

@router.get("/{item_id}", response_model=ItemDetail)
def get_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item = (db.query(Item).options(*_NOT_NEEDED)
              .filter(Item.id == item_id, Item.user_id == user.id).first())
    if not item: raise HTTPException(404, "not found")
    return item

@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: UUID, db: Session = Depends(get_db), user = Depends(get_current_user)):
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
