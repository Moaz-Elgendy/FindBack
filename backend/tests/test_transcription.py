import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from app.services import transcription


def test_local_whisper_detects_language_and_retains_times(monkeypatch):
    calls = []
    class Model:
        def __init__(self, *args, **kwargs): pass
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([SimpleNamespace(start=1.5, end=4.5, text=' استخدم هذه المهارة لكتابة الاختبارات ')]), SimpleNamespace(language='ar')
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    transcription.local_model.cache_clear()
    provider = transcription.LocalTranscriptionProvider()
    result = asyncio.run(provider.transcribe(Path('/tmp/mock.wav')))
    assert result.language == 'ar' and result.segments[0]['start'] == 1.5
    assert 'language' not in calls[0]
    transcription.local_model.cache_clear()


def test_long_content_routes_cloud(monkeypatch):
    monkeypatch.setenv('MEDIA_MAX_DURATION_SECONDS', '1200')
    assert transcription.provider_for(1201).name == 'cloud'
    assert transcription.provider_for(30).name == 'local'


def test_english_only_model_is_rejected(monkeypatch):
    monkeypatch.setenv('STT_MODEL', 'base.en')
    with pytest.raises(ValueError, match='multilingual'):
        transcription.LocalTranscriptionProvider()
