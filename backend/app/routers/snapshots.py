"""The weekly note's "Worth another look" list: GET /api/v1/snapshots/{id}.

The screen behind the note's tap target shows exactly the saves the note
counted, so this route reads the frozen ids rather than recomputing the
forgotten set (redesign addendum, section 1.3).

Two counts come back with it. `original_count` is what the note promised and
`available_count` is what is still there now: a save deleted after the note was
sent is dropped from the list, so without both numbers the user would see a
shorter list than the notification claimed, with no way to tell why.

The mobile client (`features/weekly_note/worth_another_look_page.dart`) reads
`items` from this response and shows one standard memory card per entry.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, defer

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, SnapshotItem, WeeklySnapshot
from app.schemas import ItemDetail

router = APIRouter(prefix='/api/v1/snapshots', tags=['snapshots'])

# The same columns /items never returns: the 1536-float embedding alone is
# about 12 KB of text per row, and this list is a screen of standard cards.
_NOT_NEEDED = (defer(Item.embedding), defer(Item.raw_text),
               defer(Item.normalized_text), defer(Item.chunk_texts),
               defer(Item.chunk_timestamps), defer(Item.search_text))

# What only the detail screen reads, exactly as in routers/items.py: a
# transcript can be thousands of segments and this list shows none of it.
_LIST_EXCLUDE = {"transcript", "ocr_text", "evidence_used", "processing_metadata"}


@router.get('/{snapshot_id}', response_model=dict)
def get_snapshot(snapshot_id: UUID, db: Session = Depends(get_db),
                 user = Depends(get_current_user)):
    """One snapshot's saves, in the order the note promised them.

    Scoped to the owner inside the query, so another user's snapshot id is a
    404 and never a 403 -- the existence of someone else's snapshot is not this
    endpoint's to reveal.

    Ordering is the item's own `created_at DESC, id DESC`, which is the order
    `forgotten_save_ids` returns and therefore the order the notification
    counted in. `snapshot_items` is keyed (snapshot_id, save_id) with no
    position column, so the order is derived rather than stored.
    """
    snapshot = db.query(WeeklySnapshot).filter(
        WeeklySnapshot.id == snapshot_id, WeeklySnapshot.user_id == user.id).first()
    if snapshot is None:
        raise HTTPException(404, 'not found')

    # original_count is the frozen list, including saves that have since been
    # deleted: that is the number the notification said.
    original_count = db.query(SnapshotItem).filter(
        SnapshotItem.snapshot_id == snapshot.id).count()

    rows = (db.query(Item).join(SnapshotItem, SnapshotItem.save_id == Item.id)
              .options(*_NOT_NEEDED)
              .filter(SnapshotItem.snapshot_id == snapshot.id,
                      # A save deleted after the note was sent is omitted. The
                      # owner scope is redundant with the snapshot's, and is
                      # here so a snapshot row can never surface another user's
                      # save even if it were ever written wrong.
                      Item.user_id == user.id, Item.deleted_at.is_(None))
              .order_by(Item.created_at.desc(), Item.id.desc()).all())

    return {'snapshot_id': str(snapshot.id),
            'created_at': snapshot.created_at,
            'original_count': original_count,
            'available_count': len(rows),
            'items': [ItemDetail.model_validate(row).model_dump(exclude=_LIST_EXCLUDE)
                      for row in rows]}