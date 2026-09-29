import os
import httpx
import re
from urllib.parse import urlparse

async def fetch_content(url: str, preview: str = "") -> dict:
    """
    Returns {text: str, title: str, thumbnail: str, source_type: str}
    Strategy chain: YouTube transcript -> Firecrawl -> Jina Reader -> preview fallback
    """
    domain = urlparse(url).hostname or ""
    # YouTube
    if "youtube.com" in domain or "youtu.be" in domain:
        yt = await _try_youtube(url)
        if yt: return yt
    # Firecrawl if key present
    fc_key = os.getenv("FIRECRAWL_API_KEY")
    if fc_key:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.firecrawl.dev/v1/scrape",
                    headers={"Authorization": f"Bearer {fc_key}"},
                    json={"url": url, "formats": ["markdown"]})
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    md = data.get("markdown","") or data.get("content","")
                    if md and len(md) > 200:
                        return {"text": md[:12000], "title": data.get("metadata",{}).get("title",""), "thumbnail": data.get("metadata",{}).get("ogImage","") or "", "source_type": "article"}
        except Exception:
            pass
    # Jina Reader (free)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(f"https://cc.jina.ai/{url}", headers={"Accept":"text/markdown"})
            if r.status_code == 200 and len(r.text) > 200:
                title = ""
                m = re.search(r"^Title:\s*(.+)$", r.text, re.M)
                if m: title = m.group(1).strip()
                return {"text": r.text[:12000], "title": title, "thumbnail": "", "source_type": "article"}
    except Exception:
        pass
    # Fallback to preview / minimal
    return {"text": preview or f"Link: {url}", "title": "", "thumbnail": "", "source_type": "article"}

async def _try_youtube(url: str) -> dict | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        # extract video id
        parsed = urlparse(url)
        vid = ""
        if "youtu.be" in parsed.hostname:
            vid = parsed.path.lstrip("/")
        else:
            import urllib.parse as up
            qs = dict(up.parse_qsl(parsed.query))
            vid = qs.get("v","")
        if not vid: return None
        # run sync API in thread
        import asyncio
        def _fetch():
            try: return YouTubeTranscriptApi.get_transcript(vid)
            except Exception: return None
        transcript = await asyncio.to_thread(_fetch)
        if not transcript: return None
        text = " ".join([t["text"] for t in transcript])
        # get title via oEmbed
        title = ""
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json")
                if r.status_code == 200: title = r.json().get("title","")
        except Exception: pass
        return {"text": text[:12000], "title": title, "thumbnail": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg", "source_type": "youtube"}
    except Exception:
        return None
