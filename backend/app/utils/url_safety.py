"""Public web URLs only; validate every redirect before accessing it."""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from http.cookiejar import CookieJar
from app.utils.no_cookies import NoCookies


def validate_url(value: str) -> str:
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if (parsed.scheme not in ('http', 'https') or not host or parsed.username
            or parsed.password or parsed.port not in (None, 80, 443)
            or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal'))):
        raise ValueError('Use a public HTTP or HTTPS link')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject alternate numeric IP forms, single-label hosts and malformed DNS.
        if ('.' not in host or re.fullmatch(r'[0-9xXa-fA-F.]+', host)
                or not re.fullmatch(r'[a-z0-9.-]+', host)):
            raise ValueError('Use a public website link')
    else:
        if not address.is_global or getattr(address, 'ipv4_mapped', None):
            raise ValueError('Private network links are not supported')
    return value


def public_addresses(url: str) -> list[str]:
    parsed = urlparse(validate_url(url))
    addresses = {entry[4][0] for entry in socket.getaddrinfo(
        parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80),
        type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Link resolves to a private network')
    return sorted(addresses, key=lambda address: ':' in address)


async def public_get(url: str, *, redirects: int = 5, max_bytes: int = 65536, anonymous: bool = False) -> tuple[str, httpx.Response]:
    """Pin each connection to validated DNS, preserving Host and TLS SNI."""
    url = validate_url(url)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                           cookies=CookieJar(policy=NoCookies()) if anonymous else None) as client:
        for _ in range(redirects + 1):
            addresses = await asyncio.to_thread(public_addresses, url)
            target = httpx.URL(url)
            pinned = target.copy_with(host=addresses[0])
            async with client.stream('GET', pinned, headers={'Host': target.netloc.decode()},
                                     extensions={'sni_hostname': target.host}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('Redirect has no destination')
                    url = validate_url(urljoin(url, location))
                    continue
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part[:max_bytes - len(data)])
                    if len(data) >= max_bytes:
                        break
                headers = dict(response.headers)
                headers.pop('content-encoding', None)
                headers.pop('content-length', None)
                return url, httpx.Response(response.status_code, headers=headers,
                                           content=bytes(data), request=httpx.Request('GET', url))
    raise ValueError('Too many redirects')
