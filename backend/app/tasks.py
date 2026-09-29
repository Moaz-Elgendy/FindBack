from app.celery_app import celery
from app.database import SessionLocal
from app.models import Chunk, Item
from app.services.fetcher import fetch_content
from app.services.extractor import extract_memory
from app.services.embedder import memory_string, embed_text, chunk_text
from app.services.storage import store_raw_snapshot
import datetime
import os

@celery.task(name="process_item", bind=True, max_retries=2)
def process_item(self, item_id: str):
    db = SessionLocal()
    try:
        item = db.query(Item).filter(Item.id == item_id).first()
        if not item: return {"error": "not found"}
        item.status = "processing"
        db.commit()
        # 1. Fetch
        import asyncio
        fetched = asyncio.run(fetch_content(item.url, item.raw_preview or ""))
        raw_text = fetched.get("text", "")[:12000]
        item.fetch_metadata = {k: v for k, v in fetched.items() if k != "text"}
        item.raw_s3_key = store_raw_snapshot(str(item.id), fetched)
        fetched_title = fetched.get("title","")
        thumb = fetched.get("thumbnail","")
        if thumb and not item.thumbnail_url:
            item.thumbnail_url = thumb
        # 2. Extract
        mem = asyncio.run(extract_memory(raw_text, item.title or fetched_title))
        item.title_clean = mem.title_clean or item.title
        item.summary = mem.summary
        item.key_points = mem.key_points
        item.category = mem.category
        item.entities = mem.entities
        item.intent = mem.intent
        item.tags = mem.tags
        if fetched.get("source_type"):
            item.source_type = fetched["source_type"]
        # 3. Embed
        mem_str = memory_string(item.title_clean or "", item.summary or "", item.key_points or [], item.entities or {})
        emb = asyncio.run(embed_text(mem_str))
        if emb:
            item.embedding = emb
            item.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
            db.query(Chunk).filter(Chunk.item_id == item.id).delete()
            for index, content in enumerate(chunk_text(raw_text)):
                chunk_embedding = asyncio.run(embed_text(content))
                if chunk_embedding:
                    db.add(Chunk(item_id=item.id, chunk_idx=index, chunk_text=content, embedding=chunk_embedding))
        item.status = "ready"
        item.failure_reason = None
        item.processed_at = datetime.datetime.utcnow()
        db.commit()
        return {"id": str(item.id), "status": "ready"}
    except Exception as e:
        db.rollback()
        try:
            item = db.query(Item).filter(Item.id == item_id).first()
            if item:
                item.status = "failed"
                item.failure_reason = str(e)[:1000]
                db.commit()
        except Exception: pass
        print(f"[task] process_item failed {item_id}: {e}")
        raise self.retry(exc=e, countdown=10)
    finally:
        db.close()
