import asyncio
import httpx
from app.services import media_understanding as media
from app.utils import url_safety


def test_anonymous_media_ignores_configured_cookies(monkeypatch, tmp_path):
    monkeypatch.setenv('MEDIA_COOKIES_PATH', '/private/account-cookies.txt')
    assert media.download_options(tmp_path, anonymous=True).get('cookiefile') is None
    assert media.download_options(tmp_path).get('cookiefile') == '/private/account-cookies.txt'


def test_anonymous_redirect_fetch_never_sends_response_cookies(monkeypatch):
    monkeypatch.setattr(url_safety, 'public_addresses', lambda url: ['93.184.216.34'])
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={'Location': '/article', 'Set-Cookie': 'account=private; Path=/'})
        return httpx.Response(200, text='A public article')
    original = httpx.AsyncClient
    def client(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(url_safety.httpx, 'AsyncClient', client)
    asyncio.run(url_safety.public_get('https://example.com/start', anonymous=True))
    assert len(requests) == 2
    assert all('cookie' not in request.headers and 'authorization' not in request.headers for request in requests)
