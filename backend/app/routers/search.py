from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.schemas import SearchResponse, SearchResponseItem
from app.models import Item
from app.services.intelligence import read_filters
from app.services import metrics, observability
from app.services.search import hybrid_search

router = APIRouter(prefix="/api/v1", tags=["search"])

@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=1), category: str = None, limit: int = Query(10, ge=1, le=50),
                 intelligence: dict = Depends(read_filters), db: Session = Depends(get_db), user = Depends(get_current_user)):
    # Phase 18: the query string is the user recalling something from memory,
    # so it is never logged and never used as a metric label. Only the elapsed
    # time and whether anything was found.
    with metrics.Timer(metrics.search_latency):
        results, took = await hybrid_search(db, user.id, q, category=category,
                                            limit=limit, **({'intelligence': intelligence} if isinstance(intelligence, dict) and intelligence else {}))
    metrics.search_requests.inc(labels={"outcome": "ok"})
    observability.log_event("search.served", user_id=user.id,
                            status="hit" if results else "miss",
                            duration_ms=took)
    metadata = {}
    for item in db.query(Item.id, Item.brief_v2, Item.entities, Item.intent).filter(
            Item.user_id == user.id, Item.id.in_([r['row'][0] for r in results])):
        brief = item.brief_v2 or {}
        metadata[str(item.id)] = {name: brief.get(name) for name in (
            'content_type', 'likely_intent', 'suggested_action')}
        metadata[str(item.id)].update(topics=brief.get('topics', []), entities=item.entities or {}, intent=item.intent)
    items = []
    for r in results:
        row = r["row"]
        items.append(SearchResponseItem(
            **metadata.get(str(row[0]), {}),
            id=row[0], title=row[1], summary=row[2], tags=row[3] or [], category=row[4],
            source_domain=row[5], thumbnail=row[6], created_at=row[7],
            match_reason=r["match_reason"], score=float(r["score"]),
            matched_chunk=r.get("matched_chunk"), matched_at=r.get("matched_at"),
        ))
    return SearchResponse(results=items, took_ms=took)
