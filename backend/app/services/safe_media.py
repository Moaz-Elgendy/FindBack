"""yt-dlp HTTP connections pinned to public DNS, including media and redirects."""
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.networking._requests import RequestsHTTPAdapter, RequestsRH

from app.utils import url_safety


class PublicHTTPAdapter(RequestsHTTPAdapter):
    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        target = urlparse(url_safety.validate_url(request.url))
        addresses = url_safety.public_addresses(request.url)
        request.headers['Host'] = target.netloc
        tls = {'assert_hostname': target.hostname, 'server_hostname': target.hostname} if target.scheme == 'https' else {}
        return self.poolmanager.connection_from_host(
            addresses[0], port=target.port or (443 if target.scheme == 'https' else 80),
            scheme=target.scheme, pool_kwargs=tls)


class PublicRequestsRH(RequestsRH):
    def _create_instance(self, cookiejar, legacy_ssl_support=None):
        session = super()._create_instance(cookiejar, legacy_ssl_support)
        adapter = PublicHTTPAdapter(ssl_context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support))
        session.adapters.clear()
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        return session


class PublicYoutubeDL(YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        # Keep a single guarded transport; curl/browser handlers cannot bypass it.
        return super().build_request_director([PublicRequestsRH], preferences)
