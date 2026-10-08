"""Atomic shared quotas on the existing Redis, never process-local spend caps."""
import hashlib
from functools import lru_cache

import redis
from fastapi import HTTPException

from app import env


class CapacityPause(Exception):
    def __init__(self, retry_after: int, reason: str = 'Provider capacity unavailable'):
        self.retry_after = max(1, int(retry_after))
        super().__init__(reason)


@lru_cache(maxsize=4)
def _client_for_url(url):
    return redis.Redis.from_url(url,
                               socket_connect_timeout=2, socket_timeout=2)


def redis_client():
    return _client_for_url(env.get('REDIS_URL', 'redis://localhost:6379/0'))


# Check all counters before incrementing any; rejected calls do not spend quota.
_CONSUME = """
for i,key in ipairs(KEYS) do
 local a=(i-1)*3
 if tonumber(redis.call('GET',key) or '0')+tonumber(ARGV[a+3])>tonumber(ARGV[a+1]) then
  return math.max(1,redis.call('TTL',key))
 end
end
for i,key in ipairs(KEYS) do
 local a=(i-1)*3
 redis.call('INCRBY',key,ARGV[a+3])
 if redis.call('TTL',key)<0 then redis.call('EXPIRE',key,ARGV[a+2]) end
end
return 0
"""


def consume(key: str, *, limit: int, window: int, amount: int = 1, client=None):
    consume_many([(key, limit, window, amount)], client=client)


def consume_many(counters: list[tuple[str, int, int, int]], *, client=None):
    keys = ['findback:capacity:' + key for key, _, _, _ in counters]
    arguments = [max(1, value) for _, limit, window, amount in counters for value in (limit, window, amount)]
    try:
        delay = (client or redis_client()).eval(_CONSUME, len(keys), *keys, *arguments)
    except Exception as exc:
        raise CapacityPause(60, 'Capacity checks temporarily unavailable') from exc
    if delay:
        raise CapacityPause(delay, 'Shared usage limit reached')


def enabled() -> bool:
    return env.get_bool('CAPACITY_LIMITS_ENABLED', True)


def intake_limit(identity: str, *, guest: bool, amount: int = 1):
    if not enabled():
        return
    key = hashlib.sha256(identity.encode()).hexdigest()
    limit = max(1, env.get_int('GUEST_DAILY_SAVE_LIMIT' if guest else 'ACCOUNT_DAILY_SAVE_LIMIT', 20 if guest else 100))
    try:
        consume('saves:' + key, limit=limit, window=86400, amount=amount)
    except CapacityPause as exc:
        raise HTTPException(429 if 'usage limit' in str(exc) else 503,
                            'Processing capacity is temporarily unavailable. Your link can be retried later.',
                            headers={'Retry-After': str(exc.retry_after)}) from exc


def guest_creation_limit(address: str):
    if not enabled():
        return
    key = hashlib.sha256(address.encode()).hexdigest()
    try:
        consume('guests:' + key, limit=env.get_int('GUEST_CREATIONS_PER_HOUR', 10), window=3600)
    except CapacityPause as exc:
        raise HTTPException(429 if 'usage limit' in str(exc) else 503,
                            'Please try again later.', headers={'Retry-After': str(exc.retry_after)}) from exc


def reserve_provider(provider: str, payload: dict):
    if not enabled():
        return
    client = redis_client()
    try:
        delay = client.ttl('findback:provider-pause:' + provider)
    except Exception as exc:
        raise CapacityPause(60, 'Capacity checks temporarily unavailable') from exc
    if delay > 0:
        raise CapacityPause(delay)
    import json
    measured = dict(payload)
    messages = []
    for message in payload.get('messages', []):
        content = message.get('content')
        if isinstance(content, list):
            content = [part if part.get('type') != 'image_url' else {'type': 'text', 'text': 'x' * 8192} for part in content]
        messages.append(dict(message, content=content))
    if messages:
        measured['messages'] = messages
    tokens = len(json.dumps(measured, ensure_ascii=False).encode("utf-8")) + int(payload.get("max_tokens", payload.get("max_completion_tokens", 0)) or 0)
    prefix = provider.upper()
    counters = [
        (provider + ':minute', env.get_int(prefix + '_REQUESTS_PER_MINUTE', 10), 60, 1),
        (provider + ':daily', env.get_int(prefix + '_DAILY_REQUEST_LIMIT', 500), 86400, 1),
        (provider + ':tokens', env.get_int(prefix + '_DAILY_TOKEN_LIMIT', 500000), 86400, tokens),
    ]
    spend_cap = env.get_float('AI_DAILY_BUDGET_USD', 0)
    if spend_cap > 0:
        input_price = env.get_float('BRIEF_INPUT_COST_PER_MILLION', 0)
        output_price = env.get_float('BRIEF_OUTPUT_COST_PER_MILLION', 0)
        if input_price <= 0 or output_price <= 0:
            raise CapacityPause(3600, 'Configure token prices before enabling a monetary budget')
        # Conservative reservation, including failed attempts; never undercount retries.
        cost = max(1, int(tokens * max(input_price, output_price)))
        counters.append(('all:spend', int(spend_cap * 1000000), 86400, cost))
    consume_many(counters, client=client)


def pause_provider(provider: str, seconds: int):
    if enabled():
        try:
            redis_client().set('findback:provider-pause:' + provider, '1', ex=max(1, seconds))
        except redis.RedisError:
            pass  # Reservations still fail closed if Redis is unavailable.
