import asyncio
from types import SimpleNamespace

import pytest


def test_tiktok_alias_is_video_and_final_identity_is_tiktok():
    from app.services.fetcher import video_source
    from app.services.media_understanding import source_identity
    assert video_source('https://www.tt.site/t/ZSbbndnpn/') == 'video'
    assert video_source('https://vimeo.com/123456') == 'video'
    assert video_source('https://cdn.example.com/lesson.mp4') == 'video'
    assert source_identity('https://www.tiktok.com/@author/video/7678469739454254357') == ('tiktok', '7678469739454254357')


def test_resolved_video_is_acquired_even_when_original_host_is_unknown(monkeypatch):
    from app.services import pipeline, fetcher, media_understanding as media
    called = []
    async def fetch(*args):
        return {'text': 'A useful video caption', 'input_provenance': 'caption',
                'source_type': 'video', 'resolved_url': 'https://www.tiktok.com/@author/video/123'}
    async def acquire(url, fetched, *args, **kwargs):
        called.append(url)
        return media.EvidenceBundle(url=url, source_platform='tiktok', source_id='123'), {}
    monkeypatch.setattr(fetcher, 'fetch_content', fetch)
    monkeypatch.setattr(media, 'acquire', acquire)
    item = SimpleNamespace(url='https://short.example.com/abc', title='', raw_text='',
                           raw_preview='', raw_s3_key=None, thumbnail_url=None,
                           processing_metadata={}, fetch_metadata={}, id='test')
    asyncio.run(pipeline.stage_fetch(item))
    assert called == ['https://www.tiktok.com/@author/video/123']


def test_tiktok_navigation_is_not_content():
    from app.services.fetcher import usable_text
    wrapper = 'TikTok\nCompany\nAbout\nNewsroom\nContact\nCareers\nTikTok for Good\nGet TikTok\nDownload now\nQR code\nTerms of Service\nPrivacy Policy'
    assert usable_text(wrapper, video=True) == ''


def test_extraction_commentary_is_rejected_even_without_exact_no_caption_phrase():
    from app.services import brief_v2
    from test_brief_v2 import payload, evidence
    data = payload()
    data['instant_brief'] = 'A TikTok video was saved, but the caption only contains generic navigation links and QR-code download URLs.'
    with pytest.raises(ValueError, match='commentary'):
        brief_v2.validate(data, evidence())


def test_supported_video_id_tutorial_is_not_extraction_commentary():
    from app.services import brief_v2
    from test_brief_v2 import payload, evidence
    data = payload()
    data['instant_brief'] = 'Use Claude Code to locate the video identifier in a URL.'
    ev = dict(evidence(), caption='Claude Code locates the video identifier in a URL.')
    assert brief_v2.validate(data, ev).instant_brief == data['instant_brief']


def test_unknown_duration_download_enforces_probed_cap(tmp_path, monkeypatch):
    from app.services import media_understanding as media, safe_media
    import json
    class Downloader:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def process_ie_result(self, info, download):
            (tmp_path / 'video.mp4').write_bytes(b'video')
    monkeypatch.setattr(safe_media, 'PublicYoutubeDL', Downloader)
    monkeypatch.setattr(media.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({'format': {'duration': '7201'}}).encode()))
    with pytest.raises(ValueError, match='Long-video cap'):
        media.download('https://example.com/video.mp4', {'duration': None}, tmp_path)


def test_short_video_download_does_not_drop_540p_only_visuals():
    from app.services.media_understanding import download_options
    from yt_dlp import YoutubeDL
    formats = [
        {'format_id': 'audio', 'url': 'https://example.com/audio.mp3', 'vcodec': 'none', 'acodec': 'mp3', 'ext': 'mp3'},
        {'format_id': '540p', 'url': 'https://example.com/video.mp4', 'vcodec': 'h264', 'acodec': 'aac', 'height': 1024, 'width': 576, 'ext': 'mp4'},
    ]
    options = download_options()
    with YoutubeDL({'quiet': True}) as downloader:
        selected = list(downloader.build_format_selector(options['format'])({'formats': formats, 'has_merged_format': True, 'incomplete_formats': False}))
    assert selected[0]['vcodec'] != 'none'


def test_tiktok_reader_recommendations_are_not_used_as_the_saved_video_caption(monkeypatch):
    from app.services import fetcher
    from app.utils import url_safety
    import httpx
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    monkeypatch.delenv('FIRECRAWL_API_KEY', raising=False)
    canonical = 'https://www.tiktok.com/@author/video/123456'
    async def resolve(url):
        return canonical, httpx.Response(200, text='public video', request=httpx.Request('GET', canonical))
    monkeypatch.setattr(url_safety, 'public_get', resolve)
    calls = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(200, text='Title: TikTok Shop launches on web\nMarkdown Content:\nAn unrelated shopping announcement from a recommended creator.')
    monkeypatch.setattr(fetcher.httpx, 'AsyncClient', Client)
    result = asyncio.run(fetcher.fetch_content('https://www.tt.site/t/example'))
    assert result['text'] == ''
    assert result['resolved_url'] == canonical
    assert calls == []
