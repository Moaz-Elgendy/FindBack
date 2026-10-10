import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import uuid
import pytest
from app.models import Item, ContentAsset, Chunk
from app.schemas import BriefV2
from app.services import public_cache
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


@pytest.fixture
def processing(monkeypatch, sessions):
    from app import tasks
    from app.routers import ingest
    from app.services import fetcher, brief_v2, embedder
    from test_brief_v2 import payload
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    counts = {'model': 0, 'anonymous': 0, 'enqueues': 0}

    def enqueue(*args, **kwargs):
        counts['enqueues'] += 1
        return False

    monkeypatch.setattr(ingest, '_enqueue', enqueue)
    wall = {'enabled': False}
    async def fetched(url, preview='', **kwargs):
        if kwargs.get('anonymous'):
            from app.utils.canonical import canonical_url
            assert url == canonical_url(url)
            assert not preview
            counts['anonymous'] += 1
        if wall['enabled']:
            return {'title': 'Log in to Facebook', 'text': 'Sign in to continue to view this content', 'input_provenance': 'page'}
        return {'title': 'Public growing tutorial', 'text': 'Learn how to grow vegetables using four practical steps. ' * 20,
                'source_type': 'article', 'input_provenance': 'page',
                'transcript': [{'start': 0, 'end': 10, 'text': 'Learn how to grow vegetables using four practical steps.'}]}
    async def extract(evidence):
        counts['model'] += 1
        if not wall['enabled']:
            assert 'Private' not in str(evidence)
        return BriefV2(**dict(payload(), title='Public growing tutorial'))
    async def vectors(texts):
        return [[0.01] * 1536 for _ in texts]
    monkeypatch.setattr(fetcher, 'fetch_content', fetched)
    monkeypatch.setattr(brief_v2, 'extract', extract)
    monkeypatch.setattr(embedder, 'embed_many', vectors)
    monkeypatch.setattr(embedder, 'embedding_model_name', lambda: 'test-model')
    return tasks, counts, wall


def save(client, user, url):
    client.as_user(user)
    result = client.request('POST', '/api/v1/ingest', json={'url': url, 'title_hint': 'Private title hint', 'preview': 'Private clipboard note'})
    assert result.status_code == 200, result.text
    return result.json()['id']


@pytest.mark.parametrize('url', ['https://facebook.com/posts/public1?fbclid=private-tracker', 'https://youtube.com/watch?v=dQw4w9WgXcQ'])
def test_public_social_url_saved_by_two_accounts_is_processed_once(client, sessions, two_users, processing, url):
    tasks, counts, _ = processing
    first = save(client, two_users['a'], url)
    tasks.process_item.run(first)
    second = save(client, two_users['b'], url)
    tasks.process_item.run(second)
    with sessions() as s:
        a, b = s.get(Item, uuid.UUID(first)), s.get(Item, uuid.UUID(second))
        assert a.status == b.status == 'ready'
        assert counts['model'] == 1
        assert counts['anonymous'] == 1
        assert counts['enqueues'] == 1
        assert b.processing_metadata['cache_hit']
        assert 'Private' not in str(public_cache.lookup(s, url).cache_payload)


def test_login_walled_url_is_never_shared_between_accounts(client, sessions, two_users, processing):
    tasks, counts, wall = processing
    wall['enabled'] = True
    url = 'https://facebook.com/posts/loginwall'
    for user in [two_users['a'], two_users['b']]:
        tasks.process_item.run(save(client, user, url))
    with sessions() as s:
        assert public_cache.lookup(s, url) is None
        rows = s.query(ContentAsset).filter(ContentAsset.canonical_url == url).all()
        assert len(rows) == 2
        assert all(asset.visibility != 'PUBLIC' for asset in rows)
        assert len({asset.owner_user_id for asset in rows}) == 2


def test_concurrent_owner_scoped_saves_use_one_public_processing_result(client, sessions, two_users, processing):
    tasks, counts, _ = processing
    url = 'https://facebook.com/posts/concurrent1'
    ids = [save(client, user, url) for user in [two_users['a'], two_users['b']]]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(tasks.process_item.run, ids))
    with sessions() as s:
        assert all(s.get(Item, uuid.UUID(item_id)).status == 'ready' for item_id in ids)
        assert counts['model'] == 1
        assert counts['anonymous'] == 1
        assert s.query(ContentAsset).filter(ContentAsset.cache_url == url).count() == 1


def test_batch_save_uses_complete_cache_without_enqueuing(client, sessions, two_users, processing):
    tasks, counts, _ = processing
    url = 'https://facebook.com/posts/batch1'
    tasks.process_item.run(save(client, two_users['a'], url))
    client.as_user(two_users['b'])
    result = client.request('POST', '/api/v1/sync/batch', json={'items': [{
        'client_id': 'phone-card', 'url': url, 'preview': 'Private clipboard note',
        'captured_at': datetime.now(timezone.utc).isoformat()}]})
    assert result.status_code == 200
    assert result.json()['mapped'][0]['status'] == 'ready'
    assert counts['model'] == 1
    with sessions() as s:
        item = s.get(Item, uuid.UUID(result.json()['mapped'][0]['id']))
        assert item.processing_metadata['cache_hit']
        assert s.query(Chunk).filter(Chunk.item_id == item.id).count() > 0


def test_expired_result_is_processed_fresh_for_new_account(client, sessions, two_users, processing):
    from datetime import timedelta
    tasks, counts, _ = processing
    url = 'https://facebook.com/posts/expired1'
    first = save(client, two_users['a'], url)
    tasks.process_item.run(first)
    with sessions() as s:
        asset = public_cache.lookup(s, url)
        asset.cache_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    second = save(client, two_users['b'], url)
    tasks.process_item.run(second)
    assert counts['model'] == 2
    assert counts['anonymous'] == 2
    assert counts['enqueues'] == 2
    with sessions() as s:
        assert s.get(Item, uuid.UUID(first)).status == 'ready'
        assert s.get(Item, uuid.UUID(second)).status == 'ready'
        assert public_cache.lookup(s, url) is not None


def test_failed_anonymous_probe_clears_previous_public_proof(client, sessions, two_users, processing):
    tasks, _, wall = processing
    wall['enabled'] = True
    url = 'https://facebook.com/posts/previous-proof'
    item_id = save(client, two_users['a'], url)
    with sessions() as session:
        item = session.get(Item, uuid.UUID(item_id))
        item.processing_metadata = {'anonymous_source': True}
        session.commit()
    tasks.process_item.run(item_id)
    with sessions() as session:
        item = session.get(Item, uuid.UUID(item_id))
        assert not item.processing_metadata.get('anonymous_source')
        assert public_cache.lookup(session, url) is None
        assert session.get(ContentAsset, item.content_id).owner_user_id == item.user_id
