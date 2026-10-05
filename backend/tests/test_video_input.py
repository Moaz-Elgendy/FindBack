import asyncio
from types import SimpleNamespace
import httpx
import pytest
from app.services import extractor, fetcher, pipeline, profiles


def transport(monkeypatch, respond):
    monkeypatch.setenv('FIRECRAWL_API_KEY', 'test')
    client = httpx.AsyncClient
    monkeypatch.setattr(fetcher.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))


@pytest.mark.parametrize('body', [
    'Warning: Target URL returned error 401: Unauthorized\n' + 'Sign in to continue. ' * 20,
    'Verify you are human CAPTCHA\n' + 'Privacy Terms Sign in ' * 20,
    'Title: Log in to Facebook\nMarkdown Content:\n' + 'Log in\nSign up\nForgot password?\n' * 10,
])
def test_unavailable_200_is_not_content(monkeypatch, body):
    transport(monkeypatch, lambda req: httpx.Response(401) if req.url.host == 'api.firecrawl.dev' else httpx.Response(200, text=body))
    result = asyncio.run(fetcher.fetch_content('https://www.facebook.com/share/r/example/'))
    assert result['text'] == ''
    assert result['input_provenance'] == 'none'
    assert result['source_type'] == 'video'


def test_caption_survives_page_controls(monkeypatch):
    caption = 'Five tools I use every day:\n1. Tool Alpha for writing\n2. Tool Beta for testing'
    body = 'Title: A useful reel\nMarkdown Content:\nLog in\nSign up\n' + caption + '\nPrivacy\nTerms'
    transport(monkeypatch, lambda req: httpx.Response(401) if req.url.host == 'api.firecrawl.dev' else httpx.Response(200, text=body))
    result = asyncio.run(fetcher.fetch_content('https://www.facebook.com/share/r/example/'))
    assert caption in result['text']
    assert 'Log in' not in result['text']
    assert result['input_provenance'] == 'caption'


def test_transient_failure_raises(monkeypatch):
    transport(monkeypatch, lambda req: httpx.Response(503))
    with pytest.raises(fetcher.TransientFetchError):
        asyncio.run(fetcher.fetch_content('https://example.test/article'))


def test_disabled_transcript_uses_oembed(monkeypatch):
    from youtube_transcript_api import YouTubeTranscriptApi
    def disabled(*args, **kwargs):
        raise RuntimeError('transcripts disabled')
    monkeypatch.setattr(YouTubeTranscriptApi, 'get_transcript', disabled)
    transport(monkeypatch, lambda req: httpx.Response(200, json={'title': 'Verified title', 'author_name': 'Creator'}) if req.url.path == '/oembed' else httpx.Response(404))
    result = asyncio.run(fetcher.fetch_content('https://www.youtube.com/watch?v=abc123'))
    assert result['title'] == 'Verified title'
    assert 'Creator' in result['text']
    assert result['input_provenance'] == 'caption'
    assert result['source_type'] == 'youtube'


def test_url_only_never_calls_model():
    class Gateway:
        async def generate_json(self, *args, **kwargs):
            pytest.fail('URL-only input must not reach the model')
    brief = asyncio.run(extractor.extract_brief_through(Gateway(), 'Link: https://www.example.test/x', 'Saved title'))
    assert 'could not be read' in brief.overview
    assert brief.highlights == []


def test_youtube_profile_uses_content():
    assert profiles.classify(url='https://youtube.com/watch?v=x', text='A conversation about astronomy').name == 'general'
    assert profiles.classify(url='https://youtube.com/watch?v=x', text='1. Alpha\n2. Beta\n3. Gamma').name == 'list'


def test_limited_video_brief_discloses_and_clears_inventions(monkeypatch):
    async def extract(*args, **kwargs):
        from app.schemas import Brief
        return Brief(title=args[1], overview='The caption describes two tools.', highlights=['invented'], timestamps=['01:23'])
    monkeypatch.setattr(extractor, 'extract_brief', extract)
    item = SimpleNamespace(normalized_text='Two tools for writing.', raw_text='', title='https://youtube.com/watch?v=x', url='https://youtube.com/watch?v=x', source_type='youtube', fetch_metadata={'input_provenance': 'caption', 'title': 'Verified title'})
    asyncio.run(pipeline.stage_understand(item))
    asyncio.run(pipeline.stage_brief(item))
    assert 'spoken content was unavailable' in item.summary
    assert item.key_points == []
    assert item.fetch_metadata['brief']['timestamps'] == []
    assert item.title_clean == 'Verified title'
    assert item.category == 'video'
    assert item.url in item.search_text


def test_embedded_503_retries(monkeypatch):
    transport(monkeypatch, lambda req: httpx.Response(200, text='Warning: Target URL returned error 503: Service Unavailable'))
    with pytest.raises(fetcher.TransientFetchError):
        asyncio.run(fetcher.fetch_content('https://example.test/article'))


def test_heuristic_does_not_split_url_inside_prose():
    brief = extractor._heuristic_brief('Visit https://www.example.test to read the report. More detail follows.', 'Report')
    assert 'https://www.example.test' in brief.overview


def test_caption_prompt_limits_model_to_supplied_evidence():
    class Gateway:
        async def generate_json(self, system, user, **kwargs):
            assert 'spoken content was unavailable' in system
            assert 'Leave highlights and timestamps empty' in system
            assert 'Author: Creator' in user
            return {'title': 'Video title', 'overview': 'A video by Creator.'}
    brief = asyncio.run(extractor.extract_brief_through(Gateway(), 'Video title\nAuthor: Creator', 'Video title', input_provenance='caption'))
    assert brief.title == 'Video title'


def test_enumeration_survives_whitespace_normalization():
    assert profiles.classify(text='Five choices: 1. Alpha 2. Beta 3. Gamma').name == 'list'


def test_numbered_recipe_remains_recipe():
    assert profiles.classify(text='Recipe ingredients: flour and eggs.\n1. Mix\n2. Bake').name == 'recipe'


def test_transcript_timeout_remains_transient_without_oembed(monkeypatch):
    import requests
    from youtube_transcript_api import YouTubeTranscriptApi
    def timeout(*args, **kwargs):
        raise requests.exceptions.Timeout('transcript timeout')
    monkeypatch.setattr(YouTubeTranscriptApi, 'get_transcript', timeout)
    transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(fetcher.TransientFetchError):
        asyncio.run(fetcher.fetch_content('https://youtube.com/watch?v=x'))


@pytest.mark.parametrize('saved_title,expected_title', [('https://youtube.com/watch?v=x', 'Verified title'), ('My own title', 'My own title')])
def test_fetch_preserves_video_origin_and_replaces_only_url_title(monkeypatch, saved_title, expected_title):
    async def fetched(*args):
        return {'text': 'Useful caption text', 'title': 'Verified title', 'source_type': 'article', 'input_provenance': 'caption'}
    monkeypatch.setattr(fetcher, 'fetch_content', fetched)
    monkeypatch.setattr(pipeline.storage, 'store_raw_snapshot', lambda *args: None)
    item = SimpleNamespace(id='test', url='https://youtube.com/watch?v=x', title=saved_title, source_type='youtube', thumbnail_url=None)
    asyncio.run(pipeline.stage_fetch(item))
    assert item.title == expected_title
    assert item.source_type == 'youtube'
