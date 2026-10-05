import asyncio
from sqlalchemy import text
from app.models import Item, ProcessingJob
from app.schemas import BriefV2
from app.services import brief_v2, embedder, fetcher, media_understanding, outbox, pipeline
from test_phase8_stages import admin_engine, db, sessions, _seed
from test_h3_content_reuse import _run_worker
from test_brief_v2 import payload


def test_weak_brief_stays_ready_then_retry_upgrades_and_reembeds(db, sessions, monkeypatch):
    item_id, _, job_id = _seed(sessions)
    with sessions() as session:
        item = session.get(Item, item_id)
        item.url = 'https://facebook.com/reel/upgrade'
        session.commit()
    calls = {'acquire': 0, 'embed': 0}
    async def fetch(*args):
        return {'title': 'Claude Code skills', 'text': 'Use Claude Code to write and test code.', 'input_provenance': 'caption'}
    async def acquire(url, fetched, user_id, **kwargs):
        calls['acquire'] += 1
        bundle = media_understanding.initial_bundle(url, fetched)
        if calls['acquire'] > 1:
            bundle.transcript = [media_understanding.TranscriptSegment(start=5, end=15, text=fetched['text'])]
        bundle.classify()
        return bundle, {'stt_provider': 'fake', 'cost': 0}
    async def extract(ev):
        data = payload()
        if ev['evidence_level'] != 'full_transcript':
            data.update(confidence='medium', evidence_used=['caption'], missing_info='Open original for remaining steps.',
                        key_points=[{'point': 'Claude Code writes code.', 'source_ref': 'caption'}])
        return brief_v2.validate(data, ev)
    async def embed(texts, **kwargs):
        calls['embed'] += 1
        return [[calls['embed'] / 1000] * 1536 for _ in texts]
    monkeypatch.setattr(fetcher, 'fetch_content', fetch)
    monkeypatch.setattr(media_understanding, 'acquire', acquire)
    monkeypatch.setattr(brief_v2, 'extract', extract)
    monkeypatch.setattr(embedder, 'embed_many', embed)
    monkeypatch.setattr(embedder, 'embedding_model_name', lambda: 'test-brief-model')
    monkeypatch.setattr(pipeline.storage, 'store_raw_snapshot', lambda *args: None)
    _run_worker(sessions, item_id)
    with sessions() as session:
        item, job = session.get(Item, item_id), session.get(ProcessingJob, job_id)
        assert item.status == 'ready' and item.needs_retry
        assert item.brief_v2['confidence'] == 'medium'
        assert item.brief_v2['missing_info'] not in item.search_text
        assert job.status == 'PENDING' and job.last_stage is None
        assert item.id in outbox._pending_item_ids(session, item.content_id)
        assert job.available_at > item.processed_at
        first = list(item.embedding)
        job.available_at = item.processed_at
        session.commit()
    _run_worker(sessions, item_id)
    with sessions() as session:
        item, job = session.get(Item, item_id), session.get(ProcessingJob, job_id)
        assert item.status == 'ready' and not item.needs_retry and job.status == 'READY'
        assert item.brief_v2['confidence'] == 'high' and item.brief_v2['missing_info'] is None
        assert item.evidence_bundle['evidence_level'] == 'full_transcript'
        assert item.processing_metadata['media_attempts'] == 2
        assert list(item.embedding) != first and calls == {'acquire': 2, 'embed': 2}


def test_retry_exhaustion_keeps_brief_and_stops_queue(db, sessions, monkeypatch):
    from app.services.brief_retry import schedule
    monkeypatch.setenv('MEDIA_RETRY_MAX_ATTEMPTS', '3')
    item_id, _, job_id = _seed(sessions)
    with sessions() as session:
        item, job = session.get(Item, item_id), session.get(ProcessingJob, job_id)
        item.url = 'https://facebook.com/reel/x'
        item.status = 'ready'
        item.summary = 'Known caption facts.'
        item.evidence_bundle = {'evidence_level': 'partial'}
        item.processing_metadata = {'media_attempts': 3}
        assert not schedule(session, item, job)
        assert not item.needs_retry and item.summary == 'Known caption facts.'
