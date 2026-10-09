"""Readiness must detect missing processing services, not just a live database."""
from fastapi.testclient import TestClient
import pytest
from app.services import readiness


class Redis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex):
        assert ex == readiness.MAX_AGE
        self.values[key] = value

    def mget(self, keys):
        return [self.values.get(key) for key in keys]


def test_missing_and_expired_service_heartbeats_are_not_ready():
    client = Redis()
    assert set(readiness.checks(client=client).values()) == {'down'}
    for role in readiness.ROLES:
        readiness.pulse(role, client=client)
    assert set(readiness.checks(client=client).values()) == {'ok'}
    client.values.pop(readiness.key('dispatcher'))
    assert readiness.checks(client=client)['dispatcher'] == 'down'


def test_redis_failure_is_unready_without_crashing():
    class BrokenRedis:
        def mget(self, keys):
            raise ConnectionError('offline')

        def set(self, *args, **kwargs):
            raise ConnectionError('offline')

    readiness.pulse('worker', client=BrokenRedis())
    assert set(readiness.checks(client=BrokenRedis()).values()) == {'down'}


def test_beat_tick_records_liveness_and_bounds_sleep(monkeypatch):
    pulses = []
    monkeypatch.setattr(readiness, 'pulse', pulses.append)
    monkeypatch.setattr(readiness.PersistentScheduler, 'tick', lambda self: 300)
    from celery import Celery
    scheduler = readiness.HeartbeatScheduler(app=Celery('readiness-test'), lazy=True)
    assert scheduler.tick() == 15
    assert pulses == ['beat']


@pytest.mark.parametrize('status,code', [('ok', 200), ('degraded', 503)])
def test_readiness_http_status(monkeypatch, status, code):
    from app import main
    monkeypatch.setattr(main, 'health', lambda: {'status': status})
    response = TestClient(main.app).get('/ready')
    assert response.status_code == code


def test_worker_heartbeat_signal_records_liveness(monkeypatch):
    from celery.signals import heartbeat_sent
    from app import celery_app
    pulses = []
    monkeypatch.setattr(readiness, 'pulse', pulses.append)
    heartbeat_sent.send(sender=object())
    assert pulses == ['worker']


def test_health_is_degraded_for_an_unmigrated_database(monkeypatch):
    from app import main
    from unittest.mock import MagicMock
    db = MagicMock()
    monkeypatch.setattr(main, 'SessionLocal', lambda: db)
    monkeypatch.setattr(readiness, 'schema_current', lambda session: False)
    monkeypatch.setattr(readiness, 'checks', lambda: dict.fromkeys(readiness.ROLES, 'ok'))
    monkeypatch.setattr(main.celery.backend.client, 'ping', lambda: True)
    result = main.health()
    assert result['db'] == 'ok'
    assert result['schema'] == 'outdated'
    assert result['status'] == 'degraded'


def test_new_generation_cannot_reuse_previous_release_heartbeats(monkeypatch):
    client = Redis()
    monkeypatch.setenv('PROCESSING_GENERATION', 'old')
    for role in readiness.ROLES:
        readiness.pulse(role, client=client)
    monkeypatch.setenv('PROCESSING_GENERATION', 'new')
    assert set(readiness.checks(client=client).values()) == {'down'}
    readiness.pulse('worker', client=client)
    assert readiness.checks(client=client) == {'worker': 'ok', 'dispatcher': 'down', 'beat': 'down'}


def test_rollback_still_requires_exact_migrated_schema(monkeypatch):
    from unittest.mock import MagicMock
    db = MagicMock()
    db.execute.return_value.scalars.return_value = ['new_head']
    monkeypatch.setattr(readiness, 'schema_heads', lambda: {'old_head'})
    assert not readiness.schema_current(db)
    monkeypatch.setenv('SCHEMA_READINESS_HEADS', 'new_head')
    assert readiness.schema_current(db)
    db.execute.return_value.scalars.return_value = ['unexpected_head']
    assert not readiness.schema_current(db)
