import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import text

from test_phase16_multitenant import admin_engine, db, sessions, client, _make_user


def test_atomic_budget_across_competing_clients():
    import redis
    from app.services.capacity import consume, CapacityPause
    key = 'test:' + uuid.uuid4().hex
    stores = [redis.Redis.from_url('redis://localhost:6379/0') for _ in range(2)]

    def attempt(i):
        try:
            consume(key, limit=7, window=60, client=stores[i % 2])
            return True
        except CapacityPause:
            return False

    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            assert sum(executor.map(attempt, range(30))) == 7
        assert int(stores[0].get('findback:capacity:' + key)) == 7
    finally:
        stores[0].delete('findback:capacity:' + key)


def test_intake_quota_returns_retry_after_without_losing_previous_save(client, sessions, monkeypatch):
    from app.services import capacity
    user = _make_user(sessions, 'quota@example.test', 'guest:' + str(uuid.uuid4()))
    client.as_user(user)
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('CAPACITY_LIMITS_ENABLED', 'true')
    monkeypatch.setenv('GUEST_DAILY_SAVE_LIMIT', '1')
    first = client.request('POST', '/api/v1/ingest', json={'url': 'https://example.com/first'})
    assert first.status_code == 200
    second = client.request('POST', '/api/v1/ingest', json={'url': 'https://example.com/second'})
    assert second.status_code == 429 and int(second.headers['retry-after']) > 0
    with sessions() as session:
        assert session.execute(text('SELECT count(*) FROM items')).scalar() == 1


def test_provider_pause_reschedules_without_failed_attempt(client, sessions, monkeypatch):
    from app import tasks
    from app.services.capacity import CapacityPause
    from app.models import Item, ProcessingJob
    user = _make_user(sessions, 'pause@example.test', 'pause-account')
    client.as_user(user)
    monkeypatch.setattr(tasks.process_item, 'delay', lambda *args: None)
    result = client.request('POST', '/api/v1/ingest', json={'url': 'https://example.com/pause'}).json()
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)

    async def pause(self):
        raise CapacityPause(3600)

    monkeypatch.setattr(tasks._StageProgress, 'run_all', pause)
    outcome = tasks.process_item.run(result['id'])
    assert outcome['retry_after'] == 3600
    with sessions() as session:
        item = session.get(Item, uuid.UUID(result['id']))
        job = session.query(ProcessingJob).one()
        assert item.status == 'pending' and item.failure_reason is None
        assert job.status == 'PENDING' and job.attempt_count == 0
        assert job.available_at > datetime.now(timezone.utc)
        assert job.locked_at is None and job.attempt_token is None
    assert tasks.process_item.run(result['id']).get('skipped')
