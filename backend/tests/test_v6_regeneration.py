from datetime import datetime, timezone
import uuid
from sqlalchemy import text
from app.models import ContentAsset, Item, UserMemory, ProcessingJob
from app.services import public_cache
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users
from test_v6_cache import ready_public


def test_regeneration_detaches_only_sender_and_preserves_private_note(client, sessions, two_users, monkeypatch):
    from app.routers import items as ingest
    monkeypatch.setattr(ingest, '_enqueue', lambda *args, **kwargs: False)
    with sessions() as s:
        a = ready_public(s, two_users['a_item'])
        global_asset = public_cache.publish(s, a)
        b = s.get(Item, uuid.UUID(two_users['b_item']))
        memory = s.query(UserMemory).filter(UserMemory.user_id == two_users['b']).one()
        s.add(UserMemory(user_id=b.user_id, content_id=global_asset.id, user_note=memory.user_note))
        s.flush()
        b.content_id = global_asset.id
        b.url, b.canonical_url = a.url, a.canonical_url
        public_cache.apply(s, b, global_asset)
        s.delete(memory)
        s.commit()
        old_payload = global_asset.cache_payload
    client.as_user(two_users['a'])
    response = client.request('POST', '/api/v1/items/' + two_users['a_item'] + '/summarize-again')
    assert response.status_code == 202
    assert response.json()['summary'] == 'Anonymous public summary'
    with sessions() as s:
        a, b = s.get(Item, uuid.UUID(two_users['a_item'])), s.get(Item, uuid.UUID(two_users['b_item']))
        assert a.content_id != b.content_id
        own = s.get(ContentAsset, a.content_id)
        assert own.owner_user_id == a.user_id
        assert own.visibility != 'PUBLIC'
        assert s.get(ContentAsset, b.content_id).cache_payload == old_payload
        assert b.reprocess_snapshot is None
        assert b.summary == 'Anonymous public summary'
        memory = s.query(UserMemory).filter(UserMemory.user_id == two_users['a'], UserMemory.content_id == a.content_id).one()
        assert memory.user_note == 'A private note'


def test_daily_limit_and_idempotent_pending_request(client, sessions, two_users, monkeypatch):
    from app.routers import items as ingest
    monkeypatch.setattr(ingest, '_enqueue', lambda *args, **kwargs: False)
    client.as_user(two_users['a'])
    path = '/api/v1/items/' + two_users['a_item'] + '/summarize-again'
    for count in range(1, 4):
        assert client.request('POST', path).status_code == 202
        assert client.request('POST', path).status_code == 202
        with sessions() as s:
            item = s.get(Item, uuid.UUID(two_users['a_item']))
            assert item.regeneration_count == count
            item.reprocess_snapshot = None
            item.status = 'ready'
            s.query(ProcessingJob).filter(ProcessingJob.content_id == item.content_id).update({'status': 'READY'})
            s.commit()
    denied = client.request('POST', path)
    assert denied.status_code == 429
    assert int(denied.headers['retry-after']) > 0
    with sessions() as s:
        assert s.get(Item, uuid.UUID(two_users['a_item'])).regeneration_count == 3


def test_daily_limit_can_be_configured_and_resets_on_next_day(client, sessions, two_users, monkeypatch):
    from app.routers import items as ingest
    monkeypatch.setattr(ingest, '_enqueue', lambda *args, **kwargs: False)
    monkeypatch.setenv('SUMMARIZE_AGAIN_DAILY_LIMIT', '1')
    client.as_user(two_users['a'])
    path = '/api/v1/items/' + two_users['a_item'] + '/summarize-again'
    assert client.request('POST', path).status_code == 202
    with sessions() as s:
        item = s.get(Item, uuid.UUID(two_users['a_item']))
        item.reprocess_snapshot = None
        s.query(ProcessingJob).filter(ProcessingJob.content_id == item.content_id).update({'status': 'READY'})
        s.commit()
    assert client.request('POST', path).status_code == 429
    with sessions() as s:
        item = s.get(Item, uuid.UUID(two_users['a_item']))
        from datetime import timedelta
        item.regeneration_day = datetime.now(timezone.utc).date() - timedelta(days=1)
        s.commit()
    assert client.request('POST', path).status_code == 202


def test_legacy_youtube_save_still_matches_after_detached_regeneration(client, sessions, two_users, monkeypatch):
    from app.routers import items, ingest
    monkeypatch.setattr(items, '_enqueue', lambda *args, **kwargs: False)
    monkeypatch.setattr(ingest, '_enqueue', lambda *args, **kwargs: False)
    original = 'https://youtu.be/dQw4w9WgXcQ'
    with sessions() as s:
        item = s.get(Item, uuid.UUID(two_users['a_item']))
        item.url = item.canonical_url = original
        asset = s.get(ContentAsset, item.content_id)
        asset.canonical_url = original
        asset.dedupe_key = 'youtube:dQw4w9WgXcQ'
        s.commit()
    client.as_user(two_users['a'])
    assert client.request('POST', '/api/v1/items/' + two_users['a_item'] + '/summarize-again').status_code == 202
    result = client.request('POST', '/api/v1/ingest', json={'url': 'https://youtube.com/watch?v=dQw4w9WgXcQ'})
    assert result.json()['id'] == two_users['a_item']
    assert result.json()['already_exists']
    with sessions() as s:
        assert s.get(Item, uuid.UUID(two_users['a_item'])).canonical_url == original


def test_concurrent_regeneration_requests_consume_one_quota(sessions, two_users, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from app.routers import items
    from app.schemas import SummarizeAgainRequest
    monkeypatch.setattr(items, '_enqueue', lambda *args, **kwargs: False)

    def request():
        with sessions() as session:
            item = items.summarize_again(
                uuid.UUID(two_users['a_item']), SummarizeAgainRequest(),
                session, SimpleNamespace(id=two_users['a']))
            return item.content_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: request(), range(2)))
    assert ids[0] == ids[1]
    with sessions() as session:
        item = session.get(Item, uuid.UUID(two_users['a_item']))
        assert item.regeneration_count == 1
        assert session.query(UserMemory).filter(UserMemory.user_id == item.user_id).count() == 1
