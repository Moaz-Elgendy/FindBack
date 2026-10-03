from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.auth import get_current_user
from app.models import Item
from app.schemas import ItemDetail
from app.services import retention

router = APIRouter(prefix="/api/v1/items", tags=["items"])

@router.get("", response_model=dict)
def list_items(limit: int = 20, cursor: str = None, db: Session = Depends(get_db), user = Depends(get_current_user)):
    q = db.query(Item).filter(Item.user_id == user.id)
    if cursor:
        cursor_item = db.query(Item).filter(Item.id == cursor, Item.user_id == user.id).first()
        if cursor_item:
            q = q.filter(Item.created_at < cursor_item.created_at)
    q = q.order_by(Item.created_at.desc()).limit(min(limit, 100) + 1).all()
    has_more = len(q) > limit
    items = q[:limit]
    return {"items": [ItemDetail.model_validate(i).model_dump() for i in items], "next_cursor": str(items[-1].id) if has_more and items else None}

@router.get("/{item_id}", response_model=ItemDetail)
def get_item(item_id: str, db: Session = Depends(get_db), user = Depends(get_current_user)):
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user.id).first()
    if not item: raise HTTPException(404, "not found")
    return item

@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: str, db: Session = Depends(get_db), user = Depends(get_current_user)):
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
