import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services import media_understanding as media


@pytest.mark.parametrize('transcript,caption,ocr,expected', [
    ([{'start': 0, 'end': 4, 'text': 'Four useful words spoken here.'}], '', '', 'full_transcript'),
    ([], 'Named tools from caption', '', 'partial'),
    ([], '', 'On screen list', 'partial'), ([], '', '', 'metadata_only'),
    ([{'start': 0, 'end': 1, 'text': 'Hi'}], '', '', 'metadata_only'),
])
def test_classification(transcript, caption, ocr, expected):
    bundle = media.EvidenceBundle(source_platform='facebook', url='https://facebook.com/reel/x',
                                  source_id='x', transcript=transcript, caption=caption, ocr_text=ocr)
    assert bundle.classify() == expected


def test_temp_media_is_cleaned_even_after_failure(monkeypatch):
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    paths = []
    def probe(url):
        return {'id': 'x', 'duration': 30}
    def download(url, info, directory):
        path = directory / 'video.mp4'
        path.write_bytes(b'video')
        paths.append(path)
        raise RuntimeError('download failed after writing')
    monkeypatch.setattr(media, 'probe', probe)
    monkeypatch.setattr(media, 'download', download)
    bundle, metadata = asyncio.run(media.acquire('https://facebook.com/reel/x', {'title': 'Test'}, 'owner'))
    assert bundle.evidence_level == 'metadata_only' and bundle.fetch_errors
    assert paths and not paths[0].parent.exists()


def test_download_limits_and_cookie_opt_in(monkeypatch, tmp_path):
    opts = media.download_options(tmp_path)
    assert opts['noplaylist'] and opts['max_filesize'] == 150 * 1024 * 1024
    assert '480' in opts['format'] and 'cookiefile' not in opts
    monkeypatch.setenv('MEDIA_COOKIES_PATH', '/config/cookies.txt')
    assert media.download_options(tmp_path)['cookiefile'] == '/config/cookies.txt'


def test_youtube_existing_captions_skip_download(monkeypatch):
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    monkeypatch.setattr(media, 'probe', lambda *args: pytest.fail('already has transcript'))
    fetched = {'title': 'Test', 'input_provenance': 'transcript',
               'transcript': [{'start': 0, 'end': 5, 'text': 'Use this existing transcript track.'}]}
    bundle, meta = asyncio.run(media.acquire('https://youtube.com/watch?v=x', fetched, 'owner'))
    assert bundle.evidence_level == 'full_transcript' and meta['stt_provider'] == 'captions'


def test_audio_command_uses_mono_16khz(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(media.subprocess, 'run', lambda args, **kwargs: commands.append(args))
    media.extract_audio(tmp_path / 'video.mp4', tmp_path / 'audio.wav')
    assert commands[0][commands[0].index('-ac') + 1] == '1'
    assert commands[0][commands[0].index('-ar') + 1] == '16000'
