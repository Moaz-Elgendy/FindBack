"""Content identity for deduplication (Phase 2).

`dedupe_key` is what decides whether two saves are the same piece of content.
It is computed in a fixed order, and the first tier that matches wins:

1. platform/source ID  -- `youtube:dQw4w9WgXcQ` (stable across URL forms)
2. canonical URL       -- `url:https://example.com/a`
3. normalized content  -- `sha256:...` over normalized extracted text

Tier 1 exists because the same YouTube video arrives as youtu.be/watch/embed/
shorts URLs with different tracking parameters. Tier 2 covers ordinary pages.
Tier 3 is the last resort and is only meaningful once content has been
extracted, so a URL-only save cannot produce a stable key.

The key is namespaced by kind on purpose: a YouTube video id and a page whose
URL literally reads "dQw4w9WgXcQ" must not collide.

Deliberately NOT implemented (per the phase): perceptual image/video hashing.
That is a different problem with different failure modes, and guessing at it
would be worse than not having it.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlparse

from app.utils.canonical import canonical_url

# ---------------------------------------------------------------------------
# Tier 1: platform / source IDs
# ---------------------------------------------------------------------------
# Each extractor returns the platform's own stable id for a URL, or None.
# Adding a platform is a new entry here plus, if needed, a new function; the
# ordering and the rest of the pipeline stay untouched.

_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com",
                  "youtube-nocookie.com", "www.youtube.com"}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _youtube_id(url: str) -> str | None:
    """The 11-character video id, from any of YouTube's URL shapes."""
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        candidate = p.path.lstrip("/").split("/")[0]
        return candidate if _YOUTUBE_ID.match(candidate) else None
    if host not in _YOUTUBE_HOSTS:
        return None
    if p.path in ("/watch", "/watch/"):
        params = dict(parse_qsl(p.query, keep_blank_values=True))
        candidate = params.get("v", "")
        return candidate if _YOUTUBE_ID.match(candidate) else None
    # /embed/ID, /shorts/ID, /v/ID, /live/ID
    parts = [seg for seg in p.path.split("/") if seg]
    if len(parts) >= 2 and parts[0] in ("embed", "shorts", "v", "live"):
        candidate = parts[1]
        return candidate if _YOUTUBE_ID.match(candidate) else None
    return None


# Ordered so the first match wins. A tuple of (platform, extractor).
PLATFORM_EXTRACTORS = (
    ("youtube", _youtube_id),
)


def platform_id(url: str) -> str | None:
    """Tier 1: the platform's stable id for this URL, or None if unsupported."""
    if not url:
        return None
    for platform, extract in PLATFORM_EXTRACTORS:
        try:
            value = extract(url)
        except Exception:
            value = None
        if value:
            return f"{platform}:{value}"
    return None


# ---------------------------------------------------------------------------
# Tier 3: normalized content
# ---------------------------------------------------------------------------

# Punctuation and spacing vary between two extractions of the same page
# (different scrapers, whitespace, smart quotes) without the content differing.
_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_content(text: str) -> str:
    """Casefold, strip punctuation, collapse whitespace, drop accents.

    Deterministic and dependency-free. This is *not* perceptual hashing: it
    compares text, not images or video frames.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = _NON_WORD.sub(" ", text)
    return _WS.sub(" ", text).strip()


def content_hash(text: str) -> str | None:
    """Tier 3: a hash of the normalized content, or None when there is none."""
    normalized = normalize_content(text)
    if not normalized:
        return None
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------

def dedupe_key(url: str = "", content: str = "") -> str | None:
    """Return the dedupe key for this save, or None when nothing identifies it.

    Tier order is fixed: platform id, then canonical URL, then content hash.
    """
    source = platform_id(url)
    if source:
        return source
    canon = canonical_url(url).strip() if url and url.strip() else ""
    if canon:
        return f"url:{canon}"
    return content_hash(content)


def dedupe_key_for_row(url: str = "", title: str = "", summary: str = "",
                       key_points: list | None = None) -> str | None:
    """Key for an extracted row, where text is available for tier 3.

    Still ordered platform -> URL -> content. A page with no platform id keeps
    its URL key even after extraction, so the key does not change halfway
    through the pipeline and invalidate the unique constraint.
    """
    parts = [title or "", summary or ""]
    parts.extend(str(p) for p in (key_points or []))
    return dedupe_key(url=url, content=" ".join(parts))