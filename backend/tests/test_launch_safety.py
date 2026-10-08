import asyncio
from unittest.mock import Mock

import pytest


def test_url_validation_rejects_internal_and_credentials():
    from app.utils.url_safety import validate_url
    for url in ['http://127.0.0.1/x', 'http://169.254.169.254/latest/meta-data/',
                'http://[::1]/', 'http://localhost/', 'file:///etc/passwd',
                'https://user:password@example.com/', 'http://10.0.0.1/',
                'http://2130706433/', 'https://example.com:22/']:
        with pytest.raises(ValueError):
            validate_url(url)
    assert validate_url('https://www.tt.site/t/ZSbbndnpn/').startswith('https://')


def test_dns_rejects_one_private_address_among_public(monkeypatch):
    from app.utils import url_safety
    monkeypatch.setattr(url_safety.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError):
        url_safety.public_addresses('https://example.com/')


def test_budget_is_shared_and_fails_closed():
    from app.services.capacity import consume, CapacityPause
    store = Mock()
    store.eval.side_effect = [0, 58]
    consume('same-provider', limit=2, window=60, amount=1, client=store)
    with pytest.raises(CapacityPause) as error:
        consume('same-provider', limit=2, window=60, amount=1, client=store)
    assert error.value.retry_after == 58
    assert store.eval.call_args_list[0].args[2] == store.eval.call_args_list[1].args[2]
    store.eval.side_effect = ConnectionError('redis down')
    with pytest.raises(CapacityPause):
        consume('same-provider', limit=2, window=60, client=store)


def test_budget_pause_does_not_become_a_fallback(monkeypatch):
    from app.services import brief_v2, pipeline
    from app.services.capacity import CapacityPause
    from types import SimpleNamespace

    async def exhausted(*args, **kwargs):
        raise CapacityPause(3600, 'daily budget')

    monkeypatch.setattr(brief_v2, 'extract', exhausted)
    item = SimpleNamespace(fetch_metadata={}, title='A video',
                           evidence_bundle={'title': 'A video', 'caption': 'Some useful caption'},
                           processing_metadata={}, id='test')
    with pytest.raises(CapacityPause):
        asyncio.run(pipeline.stage_understand(item))
    assert not hasattr(item, 'brief_v2')


def test_media_connections_pin_public_dns_and_keep_tls_host(monkeypatch):
    from app.utils import url_safety
    import requests
    from app.services.safe_media import PublicHTTPAdapter

    monkeypatch.setattr(url_safety, 'public_addresses', lambda url: ['8.8.8.8'])
    adapter = PublicHTTPAdapter()
    adapter.poolmanager = Mock()
    request = requests.Request('GET', 'https://cdn.example.com/video.mp4').prepare()
    adapter.get_connection_with_tls_context(request, True)
    assert request.headers['Host'] == 'cdn.example.com'
    args, kwargs = adapter.poolmanager.connection_from_host.call_args
    assert args[0] == '8.8.8.8'
    assert kwargs['pool_kwargs']['assert_hostname'] == 'cdn.example.com'
    monkeypatch.setattr(url_safety, 'public_addresses', lambda url: (_ for _ in ()).throw(ValueError('private')))
    with pytest.raises(ValueError):
        adapter.get_connection_with_tls_context(request, True)


def test_public_redirect_fetch_preserves_decoded_body(monkeypatch):
    import gzip
    import httpx
    from app.utils import url_safety
    monkeypatch.setattr(url_safety, 'public_addresses', lambda url: ['8.8.8.8'])
    real_client = httpx.AsyncClient
    def respond(request):
        if request.url.path == '/share':
            return httpx.Response(302, headers={'location': 'https://example.com/video'})
        return httpx.Response(200, headers={'content-encoding': 'gzip'}, content=gzip.compress(b'video page'))
    monkeypatch.setattr(url_safety.httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs))
    url, response = asyncio.run(url_safety.public_get('https://example.com/share'))
    assert url == 'https://example.com/video' and response.text == 'video page'
