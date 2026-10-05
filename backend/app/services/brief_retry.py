"""Evidence improvement uses the existing durable job and dispatcher."""
from app import env
from app.services import fetcher
from app.models import JOB_STATUS_PENDING
from datetime import datetime, timedelta, timezone


def delay_for(attempt: int) -> int:
    return min(3600, max(1, env.get_int('MEDIA_RETRY_BASE_SECONDS', 30)) * 2 ** min(10, max(0, attempt - 1)))


def should_retry(item) -> bool:
    return bool(fetcher.video_source(item.url or '')
                and (getattr(item, 'evidence_bundle', None) or {}).get('evidence_level') in ('partial', 'metadata_only')
                and (getattr(item, 'processing_metadata', None) or {}).get('media_attempts', 0)
                < max(1, env.get_int('MEDIA_RETRY_MAX_ATTEMPTS', 3)))


def schedule(db, item, job) -> bool:
    item.needs_retry = should_retry(item)
    if not item.needs_retry:
        db.commit()
        return False
    attempts = (item.processing_metadata or {}).get('media_attempts', 1)
    job.status = JOB_STATUS_PENDING
    job.last_stage = None
    job.locked_at = None
    job.claimed_at = None
    job.last_error = 'Media evidence improvement pending'
    job.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_for(attempts))
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True
