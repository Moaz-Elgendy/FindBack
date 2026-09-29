from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.schemas import SearchResponse, SearchResponseItem
from app.services.search import hybrid_search

router = APIRouter(prefix="/api/v1", tags=["search"])

@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=1), category: str = None, limit: int = Query(10, ge=1, le=50),
                 db: Session = Depends(get_db), user = Depends(get_current_user)):
    results, took = await hybrid_search(db, user.id, q, category=category, limit=limit)
    items = []
    for r in results:
        row = r["row"]
        items.append(SearchResponseItem(
            id=row[0], title=row[1], summary=row[2], tags=row[3] or [], category=row[4],
            source_domain=row[5], thumbnail=row[6], created_at=row[7],
            match_reason=r["match_reason"], score=float(r["score"])
        ))
    return SearchResponse(results=items, took_ms=took)
