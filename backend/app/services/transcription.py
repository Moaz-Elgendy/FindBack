"""Replaceable speech-to-text providers; language detection stays enabled."""
from __future__ import annotations

import asyncio
import tempfile
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx

from app import env
from app.services.media_understanding import TranscriptSegment


@dataclass
class TranscriptionResult:
    segments: list[dict]
    language: str | None
    cost: float | None = 0.0


class TranscriptionProvider(Protocol):
    name: str
    model: str
    async def transcribe(self, audio: Path, *, initial_prompt: str | None = None) -> TranscriptionResult: ...


@lru_cache(maxsize=1)
def local_model(model: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel
    return WhisperModel(model, device=device, compute_type=compute_type)


class LocalTranscriptionProvider:
    name = 'local'

    def __init__(self):
        self.model = env.get('STT_MODEL', 'base')
        if self.model.endswith('.en'):
            raise ValueError('STT_MODEL must be multilingual for Arabic and English')

    async def transcribe(self, audio: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        def run():
            model = local_model(self.model, env.get('STT_DEVICE', 'cpu'), env.get('STT_COMPUTE_TYPE', 'int8'))
            segments, info = model.transcribe(str(audio), vad_filter=True, beam_size=5, initial_prompt=initial_prompt)
            return TranscriptionResult([
                TranscriptSegment(start=s.start, end=s.end, text=s.text.strip()).model_dump()
                for s in segments if s.text.strip() and s.end >= s.start], info.language)
        return await asyncio.to_thread(run)


class CloudTranscriptionProvider:
    """OpenAI-compatible timestamped STT; endpoints and keys are configuration."""
    name = 'cloud'

    def __init__(self):
        self.model = env.get('STT_CLOUD_MODEL', 'whisper-1')

    async def transcribe(self, audio: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        base = env.get('STT_CLOUD_BASE_URL')
        key = env.get('STT_CLOUD_API_KEY')
        if not base or not key:
            raise ValueError('Cloud STT is not configured')
        result, language, seconds = [], None, 0.0
        # 16 kHz mono PCM is ~19 MB per ten minutes, below common upload limits.
        with wave.open(str(audio), 'rb') as source, tempfile.TemporaryDirectory(prefix='findback-stt-') as temp:
            rate = source.getframerate()
            frames_per_chunk = rate * 600
            async with httpx.AsyncClient(timeout=max(30, env.get_int('STT_CLOUD_TIMEOUT', 180))) as client:
                while True:
                    frames = source.readframes(frames_per_chunk)
                    if not frames: break
                    piece = Path(temp) / 'piece.wav'
                    with wave.open(str(piece), 'wb') as target:
                        target.setparams(source.getparams())
                        target.writeframes(frames)
                    with piece.open('rb') as stream:
                        response = await client.post(base.rstrip('/') + '/audio/transcriptions',
                            headers={'Authorization': f'Bearer {key}'},
                            data={'model': self.model, 'response_format': 'verbose_json',
                                  'timestamp_granularities[]': 'segment', **({'prompt': initial_prompt} if initial_prompt else {})},
                            files={'file': ('audio.wav', stream, 'audio/wav')})
                    response.raise_for_status()
                    data = response.json()
                    language = language or data.get('language')
                    for segment in data.get('segments', []):
                        validated = TranscriptSegment(start=seconds + segment['start'],
                            end=seconds + segment['end'], text=segment['text'].strip())
                        if validated.end >= validated.start and validated.text:
                            result.append(validated.model_dump())
                    seconds += len(frames) / (rate * source.getsampwidth() * source.getnchannels())
        price = env.get('STT_CLOUD_COST_PER_MINUTE')
        cost = seconds / 60 * env.get_float('STT_CLOUD_COST_PER_MINUTE', 0) if price else None
        return TranscriptionResult(result, language, cost)


_PROVIDERS = {'local': LocalTranscriptionProvider, 'cloud': CloudTranscriptionProvider}


def register_provider(name: str, factory) -> None:
    _PROVIDERS[name] = factory


def provider_for(duration: float | None) -> TranscriptionProvider:
    long = (duration or 0) > max(1, env.get_int('MEDIA_MAX_DURATION_SECONDS', 1200))
    name = env.get('STT_LONG_PROVIDER', 'cloud') if long else env.get('STT_PROVIDER', 'local')
    if name not in _PROVIDERS: raise ValueError('Unknown STT provider')
    return _PROVIDERS[name]()
