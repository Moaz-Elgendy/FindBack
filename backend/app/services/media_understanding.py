"""Bounded worker-only media acquisition. No downloaded file survives acquire()."""
from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, field_validator

from app import env
from app.services import observability

log = logging.getLogger('findback.media')


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str

    @field_validator('start', 'end')
    @classmethod
    def finite(cls, value):
        if not math.isfinite(value): raise ValueError('Non-finite timestamp')
        return value


class EvidenceBundle(BaseModel):
    source_platform: str
    url: str
    source_id: str
    title: str = ''
    author: str = ''
    duration: float | None = None
    caption: str = ''
    transcript: list[TranscriptSegment] = []
    ocr_text: str = ''
    frame_notes: list[str] = []
    comments: list[str] = []
    evidence_level: str = 'metadata_only'
    fetch_errors: list[str] = []
    language: str | None = None

    def classify(self) -> str:
        words = ' '.join(s.text for s in self.transcript if s.end >= s.start).split()
        if len(words) >= 4:
            self.evidence_level = 'full_transcript'
        elif self.caption.strip() or self.ocr_text.strip() or self.frame_notes:
            self.evidence_level = 'partial'
        else:
            self.evidence_level = 'metadata_only'
        return self.evidence_level


def source_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().removeprefix('www.').removeprefix('m.')
    path = parsed.path.strip('/')
    if host in ('youtube.com', 'youtu.be'):
        source_id = path if host == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
        return 'youtube', source_id or path.split('/')[-1]
    platform = {'facebook.com': 'facebook', 'fb.watch': 'facebook', 'instagram.com': 'instagram',
                'tiktok.com': 'tiktok'}.get(host, host)
    if host.endswith('.tiktok.com'): platform = 'tiktok'
    return platform, path.split('/')[-1] or url


def initial_bundle(url: str, fetched: dict) -> EvidenceBundle:
    platform, source_id = source_identity(url)
    provenance = fetched.get('input_provenance')
    bundle = EvidenceBundle(source_platform=platform, url=url, source_id=fetched.get('source_id') or source_id,
                            title=fetched.get('title') or '', author=fetched.get('author') or '',
                            duration=fetched.get('duration'), transcript=fetched.get('transcript') or [],
                            caption=(fetched.get('text') or '') if provenance in ('caption', 'page') else '')
    bundle.classify()
    return bundle


def download_options(directory: Path | None = None, *, audio_only=False) -> dict:
    cap = max(1, env.get_int('MEDIA_MAX_FILESIZE_MB', 150)) * 1024 * 1024
    options = dict(noplaylist=True, quiet=True, no_warnings=True, socket_timeout=20,
                   retries=1, fragment_retries=1, max_filesize=cap,
                   format='bestaudio/best' if audio_only else
                   'bestvideo[height<=480]+bestaudio/best[height<=480]',
                   merge_output_format='mp4', cachedir=False)
    if directory is not None:
        options['outtmpl'] = str(directory / 'media.%(ext)s')
        def limit(progress):
            if progress.get('downloaded_bytes', 0) > cap:
                raise ValueError('Media filesize cap exceeded')
            if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > cap:
                raise ValueError('Media total filesize cap exceeded')
        options['progress_hooks'] = [limit]
    cookies = env.get('MEDIA_COOKIES_PATH')
    if cookies: options['cookiefile'] = cookies
    return options


def probe(url: str) -> dict:
    from yt_dlp import YoutubeDL
    with YoutubeDL(download_options()) as downloader:
        result = downloader.extract_info(url, download=False)
    if not result or result.get('_type') in ('playlist', 'multi_video'):
        raise ValueError('Single video required')
    return result


def download(url: str, info: dict, directory: Path) -> Path:
    from yt_dlp import YoutubeDL
    duration = info.get('duration')
    if not duration or duration > max(1, env.get_int('MEDIA_LONG_MAX_SECONDS', 7200)):
        raise ValueError('Unknown duration or long-video cap exceeded')
    audio_only = duration > max(1, env.get_int('MEDIA_MAX_DURATION_SECONDS', 1200))
    with YoutubeDL(download_options(directory, audio_only=audio_only)) as downloader:
        downloader.process_ie_result(info, download=True)
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix not in ('.part', '.ytdl', '.json')]
    if not files: raise ValueError('No media downloaded')
    cap = max(1, env.get_int('MEDIA_MAX_FILESIZE_MB', 150)) * 1024 * 1024
    if sum(p.stat().st_size for p in files) > cap: raise ValueError('Media filesize cap exceeded')
    return max(files, key=lambda p: p.stat().st_size)


def extract_audio(media: Path, audio: Path) -> None:
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(media), '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)],
                   check=True, capture_output=True, timeout=max(30, env.get_int('MEDIA_FFMPEG_TIMEOUT', 300)))


def failure(bundle: EvidenceBundle, stage: str, exc: Exception) -> None:
    reason = f'{stage}: {type(exc).__name__}'
    bundle.fetch_errors.append(reason)
    log.warning('[media] platform=%s source_id=%s stage=%s failure=%s',
                bundle.source_platform, bundle.source_id, stage, observability.describe_exc(exc))


async def acquire(url: str, fetched: dict, user_id=None, *, cache_lookup=None) -> tuple[EvidenceBundle, dict]:
    began = time.monotonic()
    bundle = initial_bundle(url, fetched)
    metadata = {'stt_provider': 'captions' if bundle.transcript else None, 'stt_model': None,
                'cost': 0.0, 'cost_known': True}
    if bundle.evidence_level == 'full_transcript' or not env.get_bool('MEDIA_ENABLED', True):
        metadata.update(duration=bundle.duration, seconds_taken=time.monotonic() - began)
        return bundle, metadata
    with tempfile.TemporaryDirectory(prefix='findback-media-') as temp:
        directory = Path(temp)
        try:
            info = await asyncio.to_thread(probe, url)
            bundle.source_id = str(info.get('id') or bundle.source_id)
            bundle.title = info.get('title') or bundle.title
            bundle.author = info.get('uploader') or info.get('creator') or bundle.author
            bundle.duration = info.get('duration') or bundle.duration
            bundle.caption = info.get('description') or bundle.caption
            if cache_lookup:
                cached = cache_lookup(bundle.source_platform, bundle.source_id, user_id)
                if cached:
                    cached = EvidenceBundle(**dict(cached, url=url))
                    metadata.update(cache_hit=True, duration=cached.duration,
                                    seconds_taken=time.monotonic() - began)
                    return cached, metadata
        except Exception as exc:
            failure(bundle, 'metadata', exc)
            bundle.classify()
            metadata.update(duration=bundle.duration, seconds_taken=time.monotonic() - began)
            return bundle, metadata
        try:
            path = await asyncio.to_thread(download, url, info, directory)
        except Exception as exc:
            failure(bundle, 'download', exc)
            path = None
        if path:
            audio = directory / 'audio.wav'
            try:
                await asyncio.to_thread(extract_audio, path, audio)
            except Exception as exc:
                failure(bundle, 'audio', exc)
    bundle.classify()
    metadata.update(duration=bundle.duration, seconds_taken=time.monotonic() - began)
    return bundle, metadata
