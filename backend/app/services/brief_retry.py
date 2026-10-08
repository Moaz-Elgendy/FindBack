"""Evidence and LLM recovery use the existing durable job and dispatcher."""
from app import env
from app.services import fetcher
from app.models import JOB_STATUS_PENDING
from datetime import datetime, timedelta, timezone


def delay_for(attempt: int) -> int:
    return min(3600, max(1, env.get_int('MEDIA_RETRY_BASE_SECONDS', 30)) * 2 ** min(10, max(0, attempt - 1)))


def is_fallback(item) -> bool:
    return (getattr(item, 'brief_v2', None) or {}).get('brief_source') == 'fallback'


def should_retry(item) -> bool:
    if is_fallback(item): return True
    return bool((fetcher.video_source(item.url or '') or (getattr(item, 'evidence_bundle', None) or {}).get('source_platform') in ('youtube', 'facebook', 'instagram', 'tiktok', 'vimeo', 'dailymotion', 'twitch'))
                and (getattr(item, 'evidence_bundle', None) or {}).get('evidence_level') in ('partial', 'metadata_only')
                and (getattr(item, 'processing_metadata', None) or {}).get('media_attempts', 0)
                < max(1, env.get_int('MEDIA_RETRY_MAX_ATTEMPTS', 3)))


def schedule(db, item, job) -> bool:
    item.needs_retry = should_retry(item)
    if not item.needs_retry: return False
    fallback = is_fallback(item)
    metadata = item.processing_metadata or {}
    attempts = metadata.get('brief_attempts' if fallback else 'media_attempts', 1)
    job.status = JOB_STATUS_PENDING
    # A complete transcript needs another LLM attempt, not another download.
    job.last_stage = 'NORMALIZE' if fallback and item.evidence_bundle.get('evidence_level') == 'full_transcript' else None
    job.locked_at = None
    job.claimed_at = None
    job.last_error = 'Brief generation retry pending' if fallback else 'Media evidence improvement pending'
    job.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_for(attempts))
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True
