"""Bounded worker-only media acquisition. No downloaded file survives acquire()."""
from __future__ import annotations

import asyncio
import logging
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, field_validator

from app import env
from app.services import observability
from app.services.fetcher import clean_source_text

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
    visual_notes: list[str] = []
    comments: list[str] = []
    evidence_level: str = 'metadata_only'
    fetch_errors: list[str] = []
    language: str | None = None

    @field_validator('title', 'caption', mode='before')
    @classmethod
    def clean_source(cls, value, info):
        return clean_source_text(value or '', title=info.field_name == 'title')

    def classify(self) -> str:
        words = ' '.join(s.text for s in self.transcript if s.end >= s.start).split()
        if len(words) >= 4:
            self.evidence_level = 'full_transcript'
        elif self.caption.strip() or self.ocr_text.strip() or self.frame_notes or self.visual_notes:
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
    for name in ('facebook', 'instagram', 'tiktok'):
        if host.endswith('.' + name + '.com'): platform = name
    if platform == 'facebook' and parse_qs(parsed.query).get('v'):
        return platform, parse_qs(parsed.query)['v'][0]
    return platform, path.split('/')[-1] or url


def initial_bundle(url: str, fetched: dict) -> EvidenceBundle:
    platform, source_id = source_identity(url)
    provenance = fetched.get('input_provenance')
    bundle = EvidenceBundle(source_platform=platform, url=url, source_id=fetched.get('source_id') or source_id,
                            title=fetched.get('title') or '', author=fetched.get('author') or '',
                            duration=fetched.get('duration'), transcript=fetched.get('transcript') or [],
                            caption=fetched.get('caption', (fetched.get('text') or '') if provenance in ('caption', 'page') else '') or '')
    bundle.classify()
    return bundle


def transcription_prompt(bundle: EvidenceBundle) -> str | None:
    # Prefer explicit source keywords, then visible names, over a long caption.
    caption = clean_source_text(bundle.caption)
    keywords = re.search(r'\[keywords:\s*([^]]+)\]', caption, re.I)
    source = keywords.group(1) if keywords else clean_source_text(bundle.title, title=True) + '\n' + caption
    visible = re.findall(r'\b[A-Z][A-Z-]{2,}(?:[ \t]+[A-Z][A-Z-]{2,})*\b', bundle.ocr_text)
    text = ', '.join(visible) + '\n' + source if visible else source
    return text.strip()[:800] or None


def download_options(directory: Path | None = None, *, audio_only=False, anonymous=False) -> dict:
    cap = max(1, env.get_int('MEDIA_MAX_FILESIZE_MB', 150)) * 1024 * 1024
    options = dict(noplaylist=True, quiet=True, noprogress=True, no_warnings=True, socket_timeout=20,
                   retries=1, fragment_retries=1, max_filesize=cap,
                   format='bestaudio/best' if audio_only else
                   'bestvideo[height<=480]+bestaudio/bestvideo[width<=480]+bestaudio/best[height<=480]/best[width<=480]/best[height<=720]/best[width<=720]/bestaudio',
                   merge_output_format='mp4', cachedir=False, proxy='')
    if directory is not None:
        options['outtmpl'] = str(directory / 'media.%(ext)s')
        def limit(progress):
            if progress.get('downloaded_bytes', 0) > cap:
                raise ValueError('Media filesize cap exceeded')
            if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > cap:
                raise ValueError('Media total filesize cap exceeded')
        options['progress_hooks'] = [limit]
    cookies = env.get('MEDIA_COOKIES_PATH')
    if cookies and not anonymous: options['cookiefile'] = cookies
    if anonymous: options['_findback_anonymous'] = True
    return options


def probe(url: str, *, anonymous=False) -> dict:
    from app.services.safe_media import PublicYoutubeDL as YoutubeDL
    options = download_options(anonymous=anonymous)
    options.pop('format', None)
    with YoutubeDL(options) as downloader:
        result = downloader.extract_info(url, download=False)
    if not result or result.get('_type') in ('playlist', 'multi_video'):
        raise ValueError('Single video required')
    return result


def download(url: str, info: dict, directory: Path, *, anonymous=False) -> Path:
    from app.services.safe_media import PublicYoutubeDL as YoutubeDL
    duration = info.get('duration')
    if duration and duration > max(1, env.get_int('MEDIA_LONG_MAX_SECONDS', 7200)):
        raise ValueError('Long-video cap exceeded')
    audio_only = bool(duration and duration > max(1, env.get_int('MEDIA_MAX_DURATION_SECONDS', 1200)))
    with YoutubeDL(download_options(directory, audio_only=audio_only, anonymous=anonymous)) as downloader:
        downloader.process_ie_result(info, download=True)
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix not in ('.part', '.ytdl', '.json')]
    if not files: raise ValueError('No media downloaded')
    cap = max(1, env.get_int('MEDIA_MAX_FILESIZE_MB', 150)) * 1024 * 1024
    if sum(p.stat().st_size for p in files) > cap: raise ValueError('Media filesize cap exceeded')
    media = max(files, key=lambda p: p.stat().st_size)
    if duration is None:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
             '-show_entries', 'format=duration', '-of', 'json', str(media)],
            check=True, capture_output=True, timeout=30)
        duration = float(json.loads(probe.stdout)['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Invalid media duration')
        if duration > max(1, env.get_int('MEDIA_LONG_MAX_SECONDS', 7200)):
            raise ValueError('Long-video cap exceeded')
        info['duration'] = duration
    return media


def extract_audio(media: Path, audio: Path) -> None:
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(media), '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)],
                   check=True, capture_output=True, timeout=max(30, env.get_int('MEDIA_FFMPEG_TIMEOUT', 300)))


def failure(bundle: EvidenceBundle, stage: str, exc: Exception) -> None:
    reason = f'{stage}: {type(exc).__name__}'
    bundle.fetch_errors.append(reason)
    log.warning('[media] platform=%s source_id=%s stage=%s failure=%s',
                bundle.source_platform, bundle.source_id, stage, observability.describe_exc(exc))


async def acquire(url: str, fetched: dict, user_id=None, *, cache_lookup=None, anonymous=False) -> tuple[EvidenceBundle, dict]:
    began = time.monotonic()
    bundle = initial_bundle(url, fetched)
    metadata = {'stt_provider': 'captions' if bundle.transcript else None, 'stt_model': None,
                'cost': 0.0, 'cost_known': True}
    if not env.get_bool('MEDIA_ENABLED', True):
        metadata.update(duration=bundle.duration, seconds_taken=time.monotonic() - began)
        return bundle, metadata
    if cache_lookup:
        try:
            cached = cache_lookup(bundle.source_platform, bundle.source_id, user_id)
            if cached:
                cached = EvidenceBundle(**dict(cached, url=url, fetch_errors=[]))
                metadata.update(cache_hit=True, stt_provider='cache', duration=cached.duration,
                                seconds_taken=time.monotonic() - began)
                return cached, metadata
        except Exception as exc:
            failure(bundle, 'cache', exc)
    with tempfile.TemporaryDirectory(prefix='findback-media-') as temp:
        directory = Path(temp)
        try:
            info = await asyncio.to_thread(probe, url, **({'anonymous': True} if anonymous else {}))
            bundle.source_id = str(info.get('id') or bundle.source_id)
            bundle.title = clean_source_text(info.get('title') or bundle.title, title=True)
            bundle.author = info.get('uploader') or info.get('creator') or bundle.author
            bundle.duration = info.get('duration') or bundle.duration
            bundle.caption = clean_source_text(info.get('description') or bundle.caption)
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
            path = await asyncio.to_thread(download, url, info, directory, **({'anonymous': True} if anonymous else {}))
            bundle.duration = info.get('duration') or bundle.duration
        except Exception as exc:
            failure(bundle, 'download', exc)
            path = None
        if path:
            try:
                frames = await asyncio.to_thread(sample_frames, path, directory, bundle.duration)
            except Exception as exc:
                failure(bundle, 'frames', exc)
                frames = []
            for index, frame in enumerate(frames):
                try:
                    text = await asyncio.to_thread(read_frame, frame)
                    if text:
                        bundle.ocr_text += ('\n' if bundle.ocr_text else '') + text
                        bundle.frame_notes.append(f'Frame {index + 1}: {text}')
                except Exception as exc:
                    failure(bundle, 'ocr', exc)
            if bundle.evidence_level != 'full_transcript':
                audio = directory / 'audio.wav'
                try:
                    await asyncio.to_thread(extract_audio, path, audio)
                    from app.services.transcription import provider_for
                    provider = provider_for(bundle.duration)
                    metadata.update(stt_provider=provider.name, stt_model=provider.model)
                    stt_started = time.monotonic()
                    prompt = transcription_prompt(bundle)
                    result = await provider.transcribe(audio, initial_prompt=prompt) if prompt else await provider.transcribe(audio)
                    bundle.transcript = [TranscriptSegment(**s) for s in result.segments]
                    bundle.language = result.language
                    metadata.update(stt_prompt_used=bool(prompt), stt_seconds=time.monotonic() - stt_started, cost=result.cost,
                                    cost_known=result.cost is not None)
                except Exception as exc:
                    failure(bundle, 'audio', exc)
            if frames and len(' '.join(segment.text for segment in bundle.transcript).split()) < 25 and len(bundle.ocr_text.split()) < 20:
                from app.services.scene_understanding import describe_frames
                from app.services.capacity import CapacityPause
                try:
                    bundle.visual_notes, vision_meta = await describe_frames(frames, bundle.caption)
                    metadata.update(vision_meta)
                except CapacityPause:
                    raise
                except Exception as exc:
                    failure(bundle, 'visual', exc)
    bundle.classify()
    metadata.update(duration=bundle.duration, seconds_taken=time.monotonic() - began)
    return bundle, metadata


def sample_frames(media: Path, directory: Path, duration: float | None) -> list[Path]:
    cap = max(1, min(12, env.get_int('MEDIA_FRAME_CAP', 10)))
    output = str(directory / 'frame-%03d.jpg')
    timeout = max(30, env.get_int('MEDIA_FFMPEG_TIMEOUT', 300))
    base = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(media)]
    subprocess.run(base + ['-vf', r"select=gt(scene\,0.25),scale=720:-2", '-vsync', 'vfr',
                          '-frames:v', str(cap), '-q:v', '3', output],
                   check=True, capture_output=True, timeout=timeout)
    frames = sorted(directory.glob('frame-*.jpg'))[:cap]
    if not frames:
        interval = max(1, (duration or 30) / cap)
        subprocess.run(base + ['-vf', f'fps=1/{interval},scale=720:-2', '-frames:v', str(cap),
                              '-q:v', '3', output], check=True, capture_output=True, timeout=timeout)
        frames = sorted(directory.glob('frame-*.jpg'))[:cap]
    return frames


def read_frame(frame: Path) -> str:
    result = subprocess.run(['tesseract', str(frame), 'stdout', '-l', env.get('MEDIA_OCR_LANGUAGES', 'eng+ara')],
                            check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def cached_evidence(platform: str, source_id: str, user_id, url: str | None = None) -> dict | None:
    from sqlalchemy import or_
    from app.database import SessionLocal
    from app.models import ContentAsset, Item, VISIBILITY_PUBLIC
    # The same source id is reusable only within the existing privacy boundary.
    with SessionLocal() as db:
        candidates = (db.query(Item).outerjoin(ContentAsset, Item.content_id == ContentAsset.id)
                .filter(Item.evidence_bundle['source_platform'].astext == platform,
                        or_(Item.evidence_bundle['source_id'].astext == source_id,
                            Item.evidence_bundle['url'].astext == url) if url else
                        Item.evidence_bundle['source_id'].astext == source_id,
                        Item.evidence_bundle['evidence_level'].astext == 'full_transcript',
                        or_(Item.user_id == user_id, ContentAsset.visibility == VISIBILITY_PUBLIC))
                .order_by(Item.processed_at.desc()))
        for item in candidates:
            metadata = item.processing_metadata or {}
            evidence = dict(item.evidence_bundle)
            if (metadata.get('stt_provider') == 'local' and not metadata.get('stt_prompt_used')
                    and transcription_prompt(EvidenceBundle(**evidence))):
                continue
            return evidence
        return None
