import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

STRIP_PARAMS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid","igshid","tt_from"}

def canonical_url(url: str) -> str:
    url = url.strip()
    # A scheme means "http://" or "https://", not merely a leading "http": a bare
    # host such as httpbin.org/get starts with it too and used to be left
    # without a scheme, which urlparse then reads as a path with no host.
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    # Import here: dedupe imports this module for non-platform URL identity.
    from app.utils.dedupe import platform_id
    identity = platform_id(url)
    if identity and identity.startswith('youtube:'):
        return 'https://youtube.com/watch?v=' + identity.split(':', 1)[1]
    p = urlparse(url)
    host = p.hostname.lower() if p.hostname else ""
    # remove www.
    if host.startswith("www."):
        host = host[4:]
    # sort & strip tracking params
    qsl = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in STRIP_PARAMS]
    qsl.sort()
    query = urlencode(qsl, doseq=True)
    # normalize path: remove trailing slash except root
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    # reconstruct without fragment, with https
    netloc = host
    if p.port and p.port not in (80,443):
        netloc = f"{host}:{p.port}"
    canonical = urlunparse(("https", netloc, path, "", query, ""))
    return canonical

def source_domain(url: str) -> str:
    try:
        return urlparse(url).hostname.lower().replace("www.","") if urlparse(url).hostname else ""
    except Exception:
        return ""

def source_type(url: str) -> str:
    d = source_domain(url)
    if "youtube.com" in d or "youtu.be" in d: return "youtube"
    if "tiktok.com" in d: return "tiktok"
    if "instagram.com" in d: return "instagram"
    if "amazon." in d: return "product"
    return "article"
