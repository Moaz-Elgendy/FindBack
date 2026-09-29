from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import IngestRequest, IngestResponse, SyncBatchRequest, SyncBatchResponse
from app.models import Item
from app.utils.canonical import canonical_url, source_domain, source_type
from app.auth import get_current_user
from app.tasks import process_item

router = APIRouter(prefix="/api/v1", tags=["ingest"])

@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    canon = canonical_url(req.url)
    existing = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
    if existing:
        existing.last_seen_at = func.now()
        db.commit()
        return IngestResponse(id=existing.id, status=existing.status, canonical_url=canon)
    item = Item(
        user_id=user.id, url=req.url, canonical_url=canon,
        title=req.title_hint or req.preview[:120] if req.preview else None,
        source_domain=source_domain(req.url),
        source_type=source_type(req.url),
        status="pending",
        raw_preview=req.preview[:2000] if req.preview else None,
        summary=req.preview[:300] if req.preview else None
    )
    db.add(item); db.commit(); db.refresh(item)
    try: process_item.delay(str(item.id))
    except Exception: process_item(str(item.id))  # fallback sync if no celery
    return IngestResponse(id=item.id, status="processing", canonical_url=canon)

@router.post("/sync/batch", response_model=SyncBatchResponse)
def sync_batch(req: SyncBatchRequest, db: Session = Depends(get_db), user = Depends(get_current_user)):
    mapped, errors = [], []
    for it in req.items:
        try:
            canon = canonical_url(it.url)
            existing = db.query(Item).filter(Item.user_id == user.id, Item.canonical_url == canon).first()
            if existing:
                mapped.append({"client_id": it.client_id, "id": str(existing.id), "status": existing.status, "canonical_url": canon})
                continue
            item = Item(user_id=user.id, url=it.url, canonical_url=canon,
                        title=it.title_hint or (it.preview[:120] if it.preview else None),
                        source_domain=source_domain(it.url), source_type=source_type(it.url),
                        status="pending", raw_preview=it.preview[:2000] if it.preview else None,
                        summary=it.preview[:300] if it.preview else None)
            db.add(item); db.commit(); db.refresh(item)
            try: process_item.delay(str(item.id))
            except Exception: pass
            mapped.append({"client_id": it.client_id, "id": str(item.id), "status": "processing", "canonical_url": canon})
        except Exception as e:
            errors.append({"client_id": it.client_id, "error": str(e)})
    return SyncBatchResponse(mapped=mapped, errors=errors)
