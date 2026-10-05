"""Chat failover stays restricted to exhausted 429/503/timeouts."""
import asyncio
import json
from email.utils import formatdate

import httpx
import pytest

from app import env
from app.services import ai


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'gemini')
    monkeypatch.setenv('AI_MODEL', 'primary-model')
    monkeypatch.setenv('GEMINI_API_KEY', 'primary-real-key')
    monkeypatch.setenv('GROQ_API_KEY', 'secondary-real-key')
    monkeypatch.setenv('SECONDARY_AI_PROVIDER', 'groq')
    monkeypatch.setenv('SECONDARY_AI_MODEL', 'openai/gpt-oss-120b')
    monkeypatch.setattr(env, 'AI_MAX_ATTEMPTS', 2)
    monkeypatch.setattr(ai, 'BACKOFF_BASE', 0)
    async def no_limit(provider):
        pass
    monkeypatch.setattr(ai, '_wait_for_rate_limit', no_limit)


def success(content='{"ok": true}'):
    return httpx.Response(200, json={
        'choices': [{'message': {'content': content}}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 3}})


@pytest.mark.parametrize('failure', [429, 503, 'timeout'])
def test_failover_after_primary_attempt_cap(monkeypatch, configured, failure):
    seen = []
    def handler(request):
        seen.append((request.url.host, json.loads(request.content)))
        if request.url.host == 'generativelanguage.googleapis.com':
            if failure == 'timeout':
                raise httpx.ReadTimeout('timed out')
            return httpx.Response(failure)
        assert request.headers['authorization'] == 'Bearer secondary-real-key'
        return success()
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    usage = {}
    token = ai.CHAT_USAGE.set(usage)
    try:
        assert asyncio.run(ai.chat_json('sys', 'user', model='override')) == {'ok': True}
    finally:
        ai.CHAT_USAGE.reset(token)
    assert [host for host, _ in seen] == ['generativelanguage.googleapis.com'] * 2 + ['api.groq.com']
    assert [body['model'] for _, body in seen] == ['override', 'override', 'openai/gpt-oss-120b']
    assert usage == {'requests': 1, 'input_tokens': 7, 'output_tokens': 3,
                     'usage_reported': True, 'provider': 'groq', 'model': 'openai/gpt-oss-120b'}


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422, 500, 502, 504])
def test_other_http_errors_never_fail_over(monkeypatch, configured, status):
    seen = []
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(status)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    with pytest.raises(ai.AIHTTPError):
        asyncio.run(ai.chat_json('sys', 'user'))
    assert seen == ['generativelanguage.googleapis.com'] * (2 if status >= 500 else 1)


def test_connection_failure_does_not_fail_over(monkeypatch, configured):
    seen = []
    def handler(request):
        seen.append(request.url.host)
        raise httpx.ConnectError('no route')
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    with pytest.raises(ai.AIUnreachableError):
        asyncio.run(ai.chat_json('sys', 'user'))
    assert seen == ['generativelanguage.googleapis.com'] * 2


@pytest.mark.parametrize('header,expected', [('45', 45), (formatdate(1045, usegmt=True), 45),
                                           (formatdate(900, usegmt=True), 0)])
def test_retry_after_numeric_and_http_date(monkeypatch, header, expected):
    monkeypatch.setattr(ai.time, 'time', lambda: 1000)
    assert ai._retry_delay(httpx.Response(429, headers={'Retry-After': header}), 1) == expected


def test_primary_recovers_without_secondary(monkeypatch, configured):
    seen = []
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503) if len(seen) == 1 else success()
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    assert asyncio.run(ai.chat_json('sys', 'user')) == {'ok': True}
    assert seen == ['generativelanguage.googleapis.com'] * 2


def test_secondary_failure_is_bounded(monkeypatch, configured):
    seen = []
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    with pytest.raises(ai.AIHTTPError):
        asyncio.run(ai.chat_json('sys', 'user'))
    assert seen == ['generativelanguage.googleapis.com'] * 2 + ['api.groq.com'] * 2


def test_plain_chat_uses_same_failover(monkeypatch, configured):
    def handler(request):
        return httpx.Response(503) if request.url.host == 'generativelanguage.googleapis.com' else success('hello')
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    assert asyncio.run(ai.chat_text('sys', 'user')) == 'hello'


def test_backoff_is_awaited_before_failover(monkeypatch, configured):
    events = []
    async def sleep(delay):
        events.append(('sleep', delay))
    def handler(request):
        events.append(('post', request.url.host))
        if request.url.host == 'generativelanguage.googleapis.com':
            return httpx.Response(429, headers={'Retry-After': '45'})
        return success()
    monkeypatch.setattr(ai.asyncio, 'sleep', sleep)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    assert asyncio.run(ai.chat_json('sys', 'user')) == {'ok': True}
    assert events == [('post', 'generativelanguage.googleapis.com'), ('sleep', 45),
                      ('post', 'generativelanguage.googleapis.com'), ('post', 'api.groq.com')]


@pytest.mark.parametrize('setting', ['SECONDARY_AI_PROVIDER', 'GROQ_API_KEY'])
def test_unconfigured_secondary_preserves_primary_error(monkeypatch, configured, setting):
    monkeypatch.delenv(setting)
    seen = []
    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    with pytest.raises(ai.AIHTTPError) as error:
        asyncio.run(ai.chat_json('sys', 'user'))
    assert error.value.provider == 'gemini'
    assert seen == ['generativelanguage.googleapis.com'] * 2


def test_groq_default_is_supported_model(monkeypatch, configured):
    monkeypatch.setenv('AI_PROVIDER', 'groq')
    monkeypatch.delenv('AI_MODEL')
    assert ai.chat_config().model == 'openai/gpt-oss-120b'


@pytest.mark.parametrize('header', ['86400', formatdate(87400, usegmt=True)])
def test_long_retry_after_exhausts_budget_without_sleep(monkeypatch, configured, header):
    monkeypatch.setattr(ai.time, 'time', lambda: 1000)
    monkeypatch.setenv('AI_RETRY_WAIT_BUDGET_SECONDS', '60')
    events = []
    async def sleep(delay):
        events.append(('sleep', delay))
    def handler(request):
        events.append(('post', request.url.host))
        if request.url.host == 'generativelanguage.googleapis.com':
            return httpx.Response(429, headers={'Retry-After': header})
        return success()
    monkeypatch.setattr(ai.asyncio, 'sleep', sleep)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    assert asyncio.run(ai.chat_json('sys', 'user')) == {'ok': True}
    assert events == [('post', 'generativelanguage.googleapis.com'), ('post', 'api.groq.com')]


def test_retry_wait_budget_accumulates(monkeypatch, configured):
    monkeypatch.setenv('AI_RETRY_WAIT_BUDGET_SECONDS', '60')
    monkeypatch.setattr(env, 'AI_MAX_ATTEMPTS', 5)
    events = []
    async def sleep(delay):
        events.append(('sleep', delay))
    def handler(request):
        events.append(('post', request.url.host))
        if request.url.host == 'generativelanguage.googleapis.com':
            return httpx.Response(503, headers={'Retry-After': '40'})
        return success()
    monkeypatch.setattr(ai.asyncio, 'sleep', sleep)
    monkeypatch.setattr(ai, '_TRANSPORT', httpx.MockTransport(handler))
    assert asyncio.run(ai.chat_json('sys', 'user')) == {'ok': True}
    assert events == [('post', 'generativelanguage.googleapis.com'), ('sleep', 40),
                      ('post', 'generativelanguage.googleapis.com'), ('post', 'api.groq.com')]
