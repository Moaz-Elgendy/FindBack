"""Content retrieval must reach the real Reader endpoint when Firecrawl fails."""
import asyncio

import httpx
import pytest

from app.services import fetcher


@pytest.mark.parametrize("firecrawl", [False, True])
def test_reader_fetches_content_including_after_firecrawl_unauthorized(monkeypatch, firecrawl):
    if firecrawl:
        monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    else:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    requests = []
    markdown = "Title: A useful page\n\n" + "Actual article content. " * 20

    def respond(request):
        requests.append(request)
        if request.url.host == "api.firecrawl.dev":
            return httpx.Response(401)
        if request.url.host == "r.jina.ai":
            return httpx.Response(200, text=markdown)
        return httpx.Response(404)

    client = httpx.AsyncClient
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kwargs:
                        client(transport=httpx.MockTransport(respond), **kwargs))
    result = asyncio.run(fetcher.fetch_content("https://example.test/article"))
    assert result["text"] == markdown
    assert result["title"] == "A useful page"
    assert str(requests[-1].url) == "https://r.jina.ai/https://example.test/article"
    assert len(requests) == (2 if firecrawl else 1)


def test_reader_outage_preserves_shared_preview(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503)

    client = httpx.AsyncClient
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kwargs:
                        client(transport=httpx.MockTransport(respond), **kwargs))
    result = asyncio.run(fetcher.fetch_content("https://example.test/article", preview="Shared preview"))
    assert result["text"] == "Shared preview"
    assert requests[0].url.host == "r.jina.ai"
