"""Owner-scoped soft deletion, Undo and eventual purge."""
import datetime

from sqlalchemy import func, select

from app.models import Item
from app.services.retention import delete_save

UNDO_SECONDS = 30 * 24 * 60 * 60


def _locked_save(db, user_id, item_id):
    item = db.query(Item).filter(Item.id == item_id, Item.user_id == user_id).first()
    if item is None:
        return []
    scope = Item.content_id == item.content_id if item.content_id is not None else Item.id == item.id
    # One memory can have older URL aliases. Lock them in the same order everywhere.
    rows = (db.query(Item).filter(Item.user_id == user_id, scope)
            .order_by(Item.id).with_for_update().populate_existing().all())
    return rows if any(row.id == item.id for row in rows) else []


def _now(db):
    return db.execute(select(func.clock_timestamp())).scalar_one()


def soft_delete_save(db, user_id, item_id):
    rows = _locked_save(db, user_id, item_id)
    if not rows:
        return False
    stamp = min((row.deleted_at for row in rows if row.deleted_at is not None), default=_now(db))
    for row in rows:
        row.deleted_at = stamp
    db.commit()
    return True


def restore_save(db, user_id, item_id):
    rows = _locked_save(db, user_id, item_id)
    if not rows:
        return 'missing'
    cutoff = _now(db) - datetime.timedelta(seconds=UNDO_SECONDS)
    if any(row.deleted_at is not None and row.deleted_at <= cutoff for row in rows):
        return 'expired'
    for row in rows:
        row.deleted_at = None
    db.commit()
    return 'restored'


def purge_deleted_saves(db, limit=50):
    cutoff = _now(db) - datetime.timedelta(seconds=UNDO_SECONDS)
    candidates = (db.query(Item.id, Item.user_id).filter(Item.deleted_at <= cutoff)
                  .order_by(Item.deleted_at, Item.id).limit(limit).all())
    removed = 0
    for item_id, user_id in candidates:
        # Recheck after locking: an Undo or re-save may have won since selection.
        rows = _locked_save(db, user_id, item_id)
        if not rows or any(row.deleted_at is None or row.deleted_at > cutoff for row in rows):
            continue
        counts = delete_save(db, user_id, rows[0].content_id,
                             item_ids=[str(row.id) for row in rows] if rows[0].content_id is None else None)
        removed += counts['items']
    db.commit()
    return removed
