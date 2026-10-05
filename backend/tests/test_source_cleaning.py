import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services import fetcher, media_understanding as media, transcription

FACEBOOK = (Path(__file__).parent / 'fixtures' / 'facebook_reader.txt').read_text()


def test_facebook_cleaning_preserves_caption_and_keywords():
    cleaned = fetcher.clean_source_text(FACEBOOK)
    assert cleaned == 'Unlock the five Claude Code skills for your projects.\nComment "Claude" below, and I will send you the GitHub repos.\n[keywords: Claude Mem, Task Observer, Superpowers]'
    assert fetcher.clean_source_text('205 reactions · 8 comments | Useful Claude skills | Facebook', title=True) == 'Useful Claude skills'
    assert fetcher.usable_text(FACEBOOK, video=True) == cleaned


def test_initial_bundle_and_prompt_use_cleaned_source():
    bundle = media.initial_bundle('https://facebook.com/reel/x', {
        'title': '205 reactions · 8 comments | Claude skills | Facebook',
        'caption': FACEBOOK})
    assert bundle.title == 'Claude skills'
    bundle.ocr_text = 'IMPECCABLE'
    assert media.transcription_prompt(bundle) == 'IMPECCABLE\nClaude Mem, Task Observer, Superpowers'


def test_local_transcription_receives_source_prompt(monkeypatch):
    calls = []
    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([]), SimpleNamespace(language='en')
    monkeypatch.setattr(transcription, 'local_model', lambda *args: Model())
    asyncio.run(transcription.LocalTranscriptionProvider().transcribe(Path('/tmp/mock.wav'), initial_prompt='Claude Mem, Impeccable'))
    assert calls[0]['initial_prompt'] == 'Claude Mem, Impeccable'
    assert 'language' not in calls[0]


def test_cloud_transcription_sends_prompt(monkeypatch, tmp_path):
    import httpx
    import wave
    audio = tmp_path / 'audio.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0\0' * 160)
    calls = []
    def respond(request):
        calls.append(request.content)
        return httpx.Response(200, json={'language': 'en', 'segments': []})
    monkeypatch.setenv('STT_CLOUD_BASE_URL', 'https://stt.test/v1')
    monkeypatch.setenv('STT_CLOUD_API_KEY', 'test')
    client = httpx.AsyncClient
    monkeypatch.setattr(transcription.httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    asyncio.run(transcription.CloudTranscriptionProvider().transcribe(audio, initial_prompt='Claude Mem'))
    assert b'name="prompt"' in calls[0] and b'Claude Mem' in calls[0]


def test_acquisition_cleans_probe_and_uses_ocr_before_stt(monkeypatch):
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    monkeypatch.setattr(media, 'probe', lambda url: {'id': 'x', 'duration': 30,
        'title': '205 reactions | Claude skills | Facebook', 'description': FACEBOOK})
    def download(url, info, directory):
        path = directory / 'video.mp4'
        path.write_bytes(b'fake')
        return path
    monkeypatch.setattr(media, 'download', download)
    monkeypatch.setattr(media, 'sample_frames', lambda path, directory, duration: [path])
    monkeypatch.setattr(media, 'read_frame', lambda frame: 'IMPECCABLE')
    monkeypatch.setattr(media, 'extract_audio', lambda *args: None)
    calls = []
    class Provider:
        name = 'local'
        model = 'base'
        async def transcribe(self, audio, *, initial_prompt=None):
            calls.append(initial_prompt)
            return transcription.TranscriptionResult([], 'en')
    monkeypatch.setattr(transcription, 'provider_for', lambda duration: Provider())
    bundle, metadata = asyncio.run(media.acquire('https://facebook.com/reel/x', {}))
    assert bundle.title == 'Claude skills'
    assert bundle.caption == fetcher.clean_source_text(FACEBOOK)
    assert calls == ['IMPECCABLE\nClaude Mem, Task Observer, Superpowers']
    assert bundle.fetch_errors == []
    assert metadata['stt_prompt_used'] is True


def test_social_fetch_returns_clean_title_and_caption(monkeypatch):
    import httpx
    monkeypatch.delenv('FIRECRAWL_API_KEY', raising=False)
    client = httpx.AsyncClient
    reader = FACEBOOK.replace('Title: Facebook', 'Title: 205 reactions · 8 comments | Claude skills | Facebook')
    monkeypatch.setattr(fetcher.httpx, 'AsyncClient', lambda **kw: client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=reader)), **kw))
    result = asyncio.run(fetcher.fetch_content('https://facebook.com/reel/x'))
    assert result['title'] == 'Claude skills'
    assert result['text'] == fetcher.clean_source_text(FACEBOOK)
    assert result['input_provenance'] == 'caption'


def test_cleaning_preserves_hashtags_and_arabic_source():
    assert fetcher.clean_source_text('تعلم أدوات كتابة الكود\n#claudecode #coding') == 'تعلم أدوات كتابة الكود\n#claudecode #coding'


def test_cached_evidence_is_cleaned_before_use(monkeypatch):
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    def cached(platform, source_id, user_id):
        return {'source_platform': platform, 'url': 'https://facebook.com/reel/x',
            'source_id': source_id, 'title': '205 reactions | Claude skills | Facebook',
            'caption': FACEBOOK, 'transcript': [{'start': 0, 'end': 3, 'text': 'Four spoken words retained here.'}],
            'evidence_level': 'full_transcript'}
    monkeypatch.setattr(media, 'probe', lambda *args: (_ for _ in ()).throw(AssertionError('cached evidence skips probe')))
    bundle, metadata = asyncio.run(media.acquire('https://facebook.com/reel/x', {}, cache_lookup=cached))
    assert metadata['cache_hit']
    assert bundle.title == 'Claude skills' and bundle.caption == fetcher.clean_source_text(FACEBOOK)


def test_standalone_quantities_and_public_survive_without_social_wrapper():
    assert fetcher.clean_source_text('205\nhttps://facebook.com/help') == '205\nhttps://facebook.com/help'
    assert fetcher.clean_source_text('205\nPublic\n8') == '205\nPublic\n8'
    assert fetcher.clean_source_text('Public\n205\n8\nPeterandstewiecode · Original audio') == ''


def test_cache_skips_unprompted_local_when_source_terms_exist(monkeypatch):
    from app import database
    def candidate(provider, prompted, caption='Claude Mem'):
        return SimpleNamespace(processing_metadata={'stt_provider': provider, 'stt_prompt_used': prompted},
            evidence_bundle={'source_platform': 'facebook', 'source_id': 'x', 'url': 'https://facebook.com/reel/x',
                'caption': caption, 'transcript': [{'start': 0, 'end': 3, 'text': 'Four spoken words retained here.'}]})
    rows = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query(self, *args): return self
        def outerjoin(self, *args): return self
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def __iter__(self): return iter(rows)
    monkeypatch.setattr(database, 'SessionLocal', Session)
    old = candidate('local', False)
    prompted = candidate('local', True)
    rows[:] = [old, prompted]
    assert media.cached_evidence('facebook', 'x', 'owner') == prompted.evidence_bundle
    rows[:] = [old]
    assert media.cached_evidence('facebook', 'x', 'owner') is None
    for provider, used, caption in [('captions', False, 'Claude Mem'), (None, False, 'Claude Mem'), ('local', False, '')]:
        allowed = candidate(provider, used, caption)
        rows[:] = [allowed]
        assert media.cached_evidence('facebook', 'x', 'owner') == allowed.evidence_bundle
