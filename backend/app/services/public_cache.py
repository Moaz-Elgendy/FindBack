"""Anonymous machine results; personal copies never depend on this TTL."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import text
from app import env
from app.models import Chunk, ContentAsset, VISIBILITY_PUBLIC, JOB_STATUS_READY, JOB_TYPE_PROCESS
from app.services import brief_v2, embedder
from app.utils.canonical import canonical_url
from app.utils.dedupe import dedupe_key

FIELDS = ('title_clean', 'summary', 'key_points', 'category', 'entities', 'tags',
          'intent', 'brief_v2', 'evidence_bundle', 'source_type', 'thumbnail_url',
          'embedding', 'embedding_model')
WALL = re.compile(r'(?:log\s?in|sign in) (?:to|with)|this (?:video|post|content|account) is private|'
                  r'content (?:isn.t|is not) available|subscribe to (?:read|continue)|'
                  r'^(?:facebook|instagram|tiktok|youtube)\s*[-–:]?\s*(?:login|log in|sign in)|^private video', re.I)


def has_credentials(url):
    parsed = urlparse(url)
    return bool(parsed.username or parsed.password or any(
        re.search(r'token|secret|password|signature|credential|api.?key|authorization|^auth$|^sig$|^x-(?:amz|goog)-', key, re.I)
        for key, _ in parse_qsl(parsed.query)))


def anonymous_usable(url, fetched):
    if has_credentials(url):
        return False
    title = fetched.get('title') or ''
    body = (fetched.get('text') or '').strip()
    return (fetched.get('input_provenance') not in (None, 'none') and len(body) >= 40
            and not fetched.get('private') and not fetched.get('login_required')
            and not WALL.search(title) and not WALL.search(body[:2000]))


def ttl():
    return timedelta(days=max(1, env.get_int('PUBLIC_CACHE_TTL_DAYS', 30)))


@contextmanager
def serialized(db, url):
    # Bind processing to the lock connection so commits keep the lock without a second pool slot.
    engine, previous_bind = db.get_bind(), db.bind
    db.commit()
    with engine.connect() as connection:
        args = {'url': canonical_url(url)}
        connection.execute(text('SELECT pg_advisory_lock(hashtextextended(:url, 0))'), args)
        connection.commit()
        db.bind = connection
        try:
            yield
        finally:
            db.rollback()
            db.bind = previous_bind
            connection.execute(text('SELECT pg_advisory_unlock(hashtextextended(:url, 0))'), args)
            connection.commit()


def lookup(db, url, *, now=None):
    if has_credentials(url):
        return None
    now = now or datetime.now(timezone.utc)
    asset = (db.query(ContentAsset).filter(ContentAsset.cache_url == canonical_url(url),
             ContentAsset.visibility == VISIBILITY_PUBLIC, ContentAsset.cache_payload.isnot(None),
             ContentAsset.cache_expires_at > now).with_for_update().first())
    if asset is None:
        return None
    payload = asset.cache_payload
    if (asset.pipeline_version != JOB_TYPE_PROCESS or
            payload.get('prompt_version') != brief_v2.PROMPT_VERSION or
            payload.get('embedding_model') != embedder.embedding_model_name()):
        return None
    asset.cache_last_hit_at = now
    asset.cache_expires_at = now + ttl()
    return asset


def publish(db, item):
    if (has_credentials(item.url) or item.needs_retry or not item.summary or not item.title_clean or
            not (item.processing_metadata or {}).get('anonymous_source') or
            (item.brief_v2 or {}).get('brief_source') != 'llm'):
        return None
    url = canonical_url(item.url)
    asset = db.query(ContentAsset).filter(ContentAsset.cache_url == url).with_for_update().first()
    if asset is None:
        asset = db.query(ContentAsset).filter(ContentAsset.visibility == VISIBILITY_PUBLIC,
                  ContentAsset.dedupe_key == dedupe_key(url)).with_for_update().first()
    if asset is None:
        asset = db.get(ContentAsset, item.content_id)
    if asset is None:
        return None
    payload = {field: getattr(item, field) for field in FIELDS}
    if payload['embedding'] is not None:
        payload['embedding'] = list(map(float, payload['embedding']))
    payload['brief'] = (item.fetch_metadata or {}).get('brief') or {}
    payload['prompt_version'] = brief_v2.PROMPT_VERSION
    payload['processing_metadata'] = {key: value for key, value in (item.processing_metadata or {}).items()
                                      if key in ('prompt_version', 'brief_model', 'brief_provider')}
    payload['chunks'] = [{
        'chunk_idx': chunk.chunk_idx, 'chunk_text': chunk.chunk_text,
        'start_timestamp': chunk.start_timestamp, 'start_seconds': chunk.start_seconds,
        'embedding': list(map(float, chunk.embedding)),
    } for chunk in db.query(Chunk).filter(Chunk.item_id == item.id).order_by(Chunk.chunk_idx)]
    asset.visibility = VISIBILITY_PUBLIC
    asset.owner_user_id = None
    asset.cache_url = url
    asset.cache_payload = payload
    now = datetime.now(timezone.utc)
    asset.cache_last_hit_at = now
    asset.cache_expires_at = now + ttl()
    asset.pipeline_version = JOB_TYPE_PROCESS
    asset.processing_status = JOB_STATUS_READY
    asset.processing_error = None
    asset.title, asset.brief = item.title_clean, item.summary
    asset.structured_data = {'brief': payload['brief']}
    asset.entities, asset.topics, asset.intent = item.entities, item.tags, item.intent
    asset.processed_at = item.processed_at or now
    return asset



def prepare(db, item, job):
    import asyncio
    from copy import deepcopy
    from app.services import pipeline
    if item.reprocess_snapshot:
        return False
    if has_credentials(item.url):
        item.processing_metadata = dict(item.processing_metadata or {}, anonymous_source=False)
        return False
    cached = lookup(db, item.url)
    if cached is not None:
        apply(db, item, cached)
        return True
    if job.last_stage is not None:
        return False
    fields = ('title', 'title_clean', 'thumbnail_url', 'fetch_metadata', 'evidence_bundle',
              'processing_metadata', 'raw_text', 'raw_s3_key', 'source_type')
    saved = {field: deepcopy(getattr(item, field)) for field in fields}
    original_url = item.url
    item.url = canonical_url(item.url)
    item.title, item.title_clean, item.thumbnail_url = item.url, None, None
    item.fetch_metadata, item.evidence_bundle, item.processing_metadata = {}, {}, {}
    try:
        asyncio.run(pipeline.stage_fetch(item, anonymous=True))
        fetched = dict(item.fetch_metadata or {}, text=item.raw_text, title=item.title)
        item.url = original_url
        if anonymous_usable(original_url, fetched):
            item.processing_metadata = dict(item.processing_metadata or {}, anonymous_source=True)
            job.last_stage = pipeline.STAGE_FETCH
            db.commit()
            return False
    except Exception:
        # Failed anonymous acquisition is not proof of public visibility.
        pass
    item.url = original_url
    for field, value in saved.items():
        setattr(item, field, value)
    item.processing_metadata = dict(item.processing_metadata or {}, anonymous_source=False)
    return False

def apply(db, item, asset):
    import asyncio
    from copy import deepcopy
    from app.services import pipeline
    payload = asset.cache_payload
    if not payload:
        return False
    for field in FIELDS:
        setattr(item, field, deepcopy(payload.get(field)))
    item.fetch_metadata = dict(item.fetch_metadata or {}, brief=deepcopy(payload['brief']))
    item.processing_metadata = dict(payload['processing_metadata'], cache_hit=True)
    item.needs_retry = False
    item.status, item.failure_reason = 'ready', None
    item.processed_at = datetime.now(timezone.utc)
    asyncio.run(pipeline.stage_brief(item))
    db.query(Chunk).filter(Chunk.item_id == item.id).delete(synchronize_session=False)
    for chunk in payload['chunks']:
        db.add(Chunk(item_id=item.id, **chunk))
    return True


def cleanup(db, *, now=None, limit=200):
    now = now or datetime.now(timezone.utc)
    assets = (db.query(ContentAsset).filter(ContentAsset.cache_payload.isnot(None),
                  ContentAsset.visibility == VISIBILITY_PUBLIC,
              ContentAsset.cache_expires_at <= now).order_by(ContentAsset.cache_expires_at)
              .limit(limit).with_for_update(skip_locked=True).all())
    for asset in assets:
        asset.cache_payload = None
        asset.title = asset.brief = asset.raw_content = asset.intent = None
        asset.structured_data, asset.entities, asset.topics = {}, {}, []
    db.commit()
    return len(assets)
