import os
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.embedder import embed_text

RRF_K = int(os.getenv("RRF_K", "60"))
W_VEC = float(os.getenv("VECTOR_WEIGHT", "0.7"))
W_BM25 = float(os.getenv("BM25_WEIGHT", "0.3"))

async def hybrid_search(db: Session, user_id, query: str, category: str = None, limit: int = 10):
    import time
    t0 = time.time()
    # embed query if possible
    q_emb = await embed_text(query) if query.strip() else None

    # Vector candidates (cosine via pgvector <=> )
    vec_results = []
    if q_emb is not None:
        try:
            vec_str = "[" + ",".join(str(x) for x in q_emb) + "]"
            # items.embedding <=> query
            cat_filter = "AND category = :cat" if category else ""
            rows = db.execute(text(f"""
                SELECT id, title_clean, summary, tags, category, source_domain, thumbnail_url, created_at,
                       1 - (embedding <=> CAST(:qvec AS vector)) as cosine
                FROM items
                WHERE user_id = :uid AND status='ready' AND embedding IS NOT NULL {cat_filter}
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT 50
            """), {"uid": str(user_id), "qvec": vec_str, "cat": category} if category else {"uid": str(user_id), "qvec": vec_str}).fetchall()
            vec_results = rows
        except Exception as e:
            print(f"[search] vector failed: {e}")
            vec_results = []

    # BM25 via tsv
    bm_rows = []
    try:
        # use plainto_tsquery for natural language
        cat_filter2 = "AND category = :cat" if category else ""
        bm_rows = db.execute(text(f"""
            SELECT id, title_clean, summary, tags, category, source_domain, thumbnail_url, created_at,
                   ts_rank(tsv, plainto_tsquery('english', :q)) as rank
            FROM items
            WHERE user_id = :uid AND status='ready' AND tsv @@ plainto_tsquery('english', :q) {cat_filter2}
            ORDER BY rank DESC
            LIMIT 50
        """), {"uid": str(user_id), "q": query, "cat": category} if category else {"uid": str(user_id), "q": query}).fetchall()
    except Exception as e:
        print(f"[search] bm25 failed: {e}")

    # RRF fusion
    rank_vec = {str(r[0]): i+1 for i, r in enumerate(vec_results)}
    rank_bm  = {str(r[0]): i+1 for i, r in enumerate(bm_rows)}
    vec_map = {str(r[0]): r for r in vec_results}
    bm_map  = {str(r[0]): r for r in bm_rows}
    all_ids = set(rank_vec) | set(rank_bm)
    # if no vector (no key), fallback to keyword + recency for offline/dev
    if not q_emb or not vec_results:
        if not bm_rows:
            # last resort: LIKE substring over title/summary
            try:
                like_rows = db.execute(text("""
                    SELECT id, title_clean, summary, tags, category, source_domain, thumbnail_url, created_at, 0.0 as rank
                    FROM items WHERE user_id=:uid AND status='ready'
                    AND (title_clean ILIKE :pat OR summary ILIKE :pat)
                    ORDER BY created_at DESC LIMIT :lim
                """), {"uid": str(user_id), "pat": f"%{query}%", "lim": limit}).fetchall()
                took = int((time.time()-t0)*1000)
                return [{"row": r, "score": 0.5 - i*0.01, "match_reason": f"Matched: {query}"} for i,r in enumerate(like_rows)], took
            except Exception as e:
                print(f"[search] like fallback failed: {e}")
                return [], int((time.time()-t0)*1000)
        else:
            took = int((time.time()-t0)*1000)
            return [{"row": r, "score": float(r[-1] if len(r)>0 else 0), "match_reason": f"Matched: {query}"} for r in bm_rows[:limit]], took

    scored = []
    for iid in all_ids:
        rv = rank_vec.get(iid)
        rb = rank_bm.get(iid)
        s = 0.0
        if rv: s += W_VEC * (1.0 / (RRF_K + rv))
        if rb: s += W_BM25 * (1.0 / (RRF_K + rb))
        row = vec_map.get(iid) or bm_map.get(iid)
        scored.append((iid, s, row))
    scored.sort(key=lambda x: x[1], reverse=True)

    # Build results with match_reason from tags/entities overlap
    results = []
    for iid, score, row in scored[:limit]:
        # row is tuple from SELECT; extract fields by index: 0:id,1:title_clean,2:summary,3:tags,4:category,5:source_domain,6:thumbnail,7:created_at,8:cosine/rank
        q_lower = query.lower()
        tags = row[3] or []
        # find overlapping tag/entity words
        matched = [t for t in tags if t.lower() in q_lower] if tags else []
        reason = f"Matched: {', '.join(matched)}" if matched else f"Matched: { (row[4] or 'memory') }"
        results.append({"row": row, "score": score, "match_reason": reason})
    took = int((time.time()-t0)*1000)
    return results, took
