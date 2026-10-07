import asyncio
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout


class TransientFetchError(RuntimeError):
    """No usable fallback survived a temporary provider outage."""


def video_source(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com'):
        return 'youtube'
    if host == 'instagram.com' or host.endswith('.instagram.com') or host == 'fb.watch':
        return 'video'
    if host == 'tiktok.com' or host.endswith('.tiktok.com'):
        return 'video'
    if (host == 'facebook.com' or host.endswith('.facebook.com')) and re.search(r'/(?:reel|reels|watch|videos|share/r)(?:/|$)', parsed.path):
        return 'video'
    return None


def url_only(text: str) -> bool:
    return bool(re.fullmatch(r'(?:Link:\s*)?https?://\S+\s*', text.strip(), re.I))


def clean_source_text(text: str, *, title: bool = False) -> str:
    """Keep source prose while removing social UI and reader markup."""
    social_wrapper = bool(re.search(r'^URL Source:\s*https?://(?:[^/]+\.)?facebook\.com/|original audio|see more on facebook|email or phone number', text or '', re.I | re.M))
    text = re.sub(r'!\[[^]]*\]\([^)]*\)|!\[[^]]*\]\[[^]]*\]', '', text or '')
    text = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'(?im)^\s*\[[^]]+\]:\s*https?://\S+.*$', '', text)
    text = text.split('Markdown Content:', 1)[-1]
    controls = r'reels?|.+ sent you (?:a|an) (?:reel|video|post|link)|log\s?in|sign\s?(?:in|up)|forgot (?:password|account)\??|privacy|terms|log in to .+|see more(?: on Facebook)?|see less|like|comment|share|email or phone number|password|create new account|(?:.+\s*[·|–-]\s*)?original audio(?:\s*[·|–-].*)?'
    counts = r'(?:[\d,.]+\s*[KMB]?\s*(?:reactions?|likes?|comments?|shares?|views?))(?:\s*[·|]\s*[\d,.]+\s*[KMB]?\s*(?:reactions?|likes?|comments?|shares?|views?))*'
    lines = []
    for line in text.splitlines():
        plain = re.sub(r'^#{1,6}\s+', '', line.strip(' *\t'))
        if re.fullmatch(controls, plain, re.I) or re.fullmatch(counts, plain, re.I):
            continue
        if social_wrapper and (plain.lower() == 'public' or re.fullmatch(r'[\d,.]+\s*[KMB]?', plain, re.I)):
            continue
        if plain.startswith(('Title:', 'URL Source:', 'Image ', 'Published Time:')):
            continue
        plain = re.sub(r'(?i)^' + counts + r'\s*[|·]\s*', '', plain)
        if title:
            if plain.lower() in ('facebook', 'instagram', 'tiktok', 'youtube', 'log in', 'login'):
                continue
            plain = re.sub(r'(?i)\s*[|–-]\s*(?:Facebook|Instagram|TikTok|YouTube)\s*$', '', plain)
        if plain:
            lines.append(plain)
    return '\n'.join(lines).strip()


def usable_text(text: str, video: bool = False) -> str:
    """Reject error wrappers; for social pages keep prose, not navigation."""
    if not text.strip() or url_only(text):
        return ''
    error = re.search(r'Target URL returned error\s+(\d{3})', text, re.I)
    if error:
        if int(error.group(1)) >= 500:
            raise TransientFetchError('Upstream page temporarily unavailable')
        return ''
    if re.search(r'verify you are human|captcha|video (?:currently )?unavailable', text, re.I):
        return ''
    body = text.split('Markdown Content:', 1)[-1] if video else text
    controls = r'log\s?in|sign\s?(?:in|up)|forgot password\??|privacy|terms|log in to .+'
    lines = []
    for line in body.splitlines():
        plain = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', line).strip(' #*')
        if re.fullmatch(controls, plain, re.I) or plain.startswith(('Title:', 'URL Source:')):
            continue
        lines.append(line)
    cleaned = clean_source_text('\n'.join(lines)) if video else '\n'.join(lines).strip()
    if not cleaned or url_only(cleaned):
        return ''
    if video or re.search(r'log\s?in|sign in', text, re.I):
        if len(re.findall(r'\b\w+\b', cleaned)) < 5:
            return ''
        return cleaned
    return text


async def fetch_content(url: str, preview: str = '') -> dict:
    source = video_source(url)
    transient = False
    limited = None
    if source == 'youtube':
        try:
            limited = await _try_youtube(url)
        except TransientFetchError:
            transient = True
        if limited and limited['input_provenance'] == 'transcript':
            return limited
    key = os.getenv('FIRECRAWL_API_KEY')
    providers = [('firecrawl', key)] if key else []
    providers.append(('reader', None))
    for provider, key in providers:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                if provider == 'firecrawl':
                    response = await client.post('https://api.firecrawl.dev/v1/scrape', headers={'Authorization': f'Bearer {key}'}, json={'url': url, 'formats': ['markdown']})
                else:
                    response = await client.get(f'https://r.jina.ai/{url}', headers={'Accept': 'text/markdown'})
                if response.status_code >= 500:
                    transient = True
                if response.status_code != 200:
                    continue
                if provider == 'firecrawl':
                    data = response.json().get('data', {})
                    text = data.get('markdown') or data.get('content') or ''
                    meta = data.get('metadata') or {}
                    title, thumbnail = meta.get('title', ''), meta.get('ogImage', '')
                else:
                    text = response.text
                    match = re.search(r'^Title:\s*(.+)$', text, re.M)
                    title, thumbnail = (match.group(1).strip() if match else ''), ''
                text = usable_text(text, bool(source))
                if text:
                    return {'text': text[:12000], 'title': clean_source_text((limited or {}).get('title') or title, title=True) if source else ((limited or {}).get('title') or title), 'thumbnail': thumbnail or '', 'source_type': source or 'article', 'input_provenance': 'caption' if source else 'page'}
        except (httpx.TimeoutException, httpx.TransportError, TransientFetchError):
            transient = True
        except (ValueError, TypeError):
            continue
    if limited:
        return limited
    text = usable_text(preview, bool(source))
    if not text and transient:
        raise TransientFetchError('Content providers temporarily unavailable')
    return {'text': text[:12000], 'title': '', 'thumbnail': '', 'source_type': source or 'article', 'input_provenance': ('caption' if source else 'page') if text else 'none'}


async def _try_youtube(url: str) -> dict | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    parsed = urlparse(url)
    vid = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
    if not vid and parsed.path.startswith(('/shorts/', '/embed/')):
        vid = parsed.path.split('/')[2]
    if not vid:
        return None
    transient = False
    try:
        transcript = await asyncio.to_thread(YouTubeTranscriptApi.get_transcript, vid, languages=['ar', 'en'])
    except Exception as exc:
        transcript = None
        transient = (isinstance(exc, (httpx.TimeoutException, TimeoutError, RequestsTimeout, RequestsConnectionError))
                     or (getattr(getattr(exc, "response", None), "status_code", 0) or 0) >= 500)
    title = author = ''
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get('https://www.youtube.com/oembed', params={'url': f'https://www.youtube.com/watch?v={vid}', 'format': 'json'})
            if response.status_code >= 500:
                transient = True
            if response.status_code == 200:
                data = response.json()
                title, author = data.get('title', ''), data.get('author_name', '')
    except (httpx.TimeoutException, httpx.TransportError):
        transient = True
    except (ValueError, TypeError):
        pass
    segments = [{'start': s['start'], 'end': s['start'] + s.get('duration', 0), 'text': s['text']} for s in (transcript or [])]
    text = ' '.join(f"[{int(s['start']) // 60:02d}:{int(s['start']) % 60:02d}] {s['text']}" for s in segments) if transcript else '\n'.join(value for value in (title, f'Author: {author}' if author else '') if value)
    if not text:
        if transient:
            raise TransientFetchError('YouTube temporarily unavailable')
        return None
    return {'text': text[:12000], 'title': title, 'thumbnail': f'https://img.youtube.com/vi/{vid}/hqdefault.jpg', 'source_type': 'youtube', 'input_provenance': 'transcript' if transcript else 'caption', 'transcript': segments, 'author': author, 'source_id': vid, 'caption': ''}
