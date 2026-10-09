"""Processing readiness from expiring heartbeats on the existing Redis."""
import logging
import os
from functools import lru_cache
from celery.beat import PersistentScheduler
from alembic.script import ScriptDirectory
from sqlalchemy import text
from app.database import alembic_config
from app.services.capacity import redis_client

ROLES = ('worker', 'dispatcher', 'beat')
MAX_AGE = 60
log = logging.getLogger('findback.readiness')


def key(role):
    return 'findback:service:' + os.environ.get('PROCESSING_GENERATION', 'local') + ':' + role


def pulse(role, *, client=None):
    try:
        (client or redis_client()).set(key(role), 'ok', ex=MAX_AGE)
    except Exception as exc:
        log.warning('%s heartbeat failed: %s', role, type(exc).__name__)


def checks(*, client=None):
    try:
        values = (client or redis_client()).mget([key(role) for role in ROLES])
        return {role: 'ok' if value else 'down' for role, value in zip(ROLES, values)}
    except Exception:
        return dict.fromkeys(ROLES, 'down')


@lru_cache(maxsize=1)
def schema_heads():
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def schema_current(db):
    expected = os.environ.get('SCHEMA_READINESS_HEADS')
    heads = set(expected.split(',')) if expected else schema_heads()
    return set(db.execute(text('SELECT version_num FROM alembic_version')).scalars()) == heads


class HeartbeatScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        delay = super().tick(*args, **kwargs)
        pulse('beat')
        return min(delay, 15)
