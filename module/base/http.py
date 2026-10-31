"""HTTP/3-first runtime transport with shared QUIC capability and fallback state."""

from urllib.parse import urlsplit

import niquests
from niquests import RequestException as RequestException
from niquests import exceptions as exceptions
from niquests.structures import CaseInsensitiveDict, QuicSharedCache

from deploy.http import BROWSER_USER_AGENT as BROWSER_USER_AGENT
from deploy.http import DOWNLOAD_HEADERS as DOWNLOAD_HEADERS
from deploy.http import GIT_HTTP_OPTIONS as GIT_HTTP_OPTIONS
from deploy.http import USER_AGENT as USER_AGENT
from deploy.http import download_file as download_file
from deploy.http import is_service_url as is_service_url

_QUIC_CACHE = QuicSharedCache(max_size=12288)


def _prepare(url, headers, download):
    headers = CaseInsensitiveDict(headers or {})
    if download:
        # Preserve functional overrides such as Range and Accept-Encoding: identity.
        for key, value in DOWNLOAD_HEADERS.items():
            headers.setdefault(key, value)
        headers['User-Agent'] = BROWSER_USER_AGENT
    elif is_service_url(url):
        headers['User-Agent'] = USER_AGENT
    if is_service_url(url):
        parsed = urlsplit(url)
        if parsed.scheme == 'https':
            _QUIC_CACHE.add_domain(parsed.hostname, port=parsed.port or 443)
    return headers


class Session(niquests.Session):
    def __init__(self, *, download=False, **kwargs):
        kwargs.setdefault('quic_cache_layer', _QUIC_CACHE)
        super().__init__(**kwargs)
        self.download = download

    def request(self, method, url, *, headers=None, download=None, **kwargs):
        headers = _prepare(url, headers, self.download if download is None else download)
        return super().request(method, url, headers=headers, **kwargs)


class AsyncSession(niquests.AsyncSession):
    def __init__(self, *, download=False, **kwargs):
        kwargs.setdefault('quic_cache_layer', _QUIC_CACHE)
        super().__init__(**kwargs)
        self.download = download

    async def request(self, method, url, *, headers=None, download=None, **kwargs):
        headers = _prepare(url, headers, self.download if download is None else download)
        return await super().request(method, url, headers=headers, **kwargs)


def request(method, url, *, download=False, **kwargs):
    with Session(download=download) as session:
        return session.request(method, url, **kwargs)


def get(url, **kwargs):
    kwargs.setdefault('allow_redirects', True)
    return request('GET', url, **kwargs)


def post(url, **kwargs):
    return request('POST', url, **kwargs)


def head(url, **kwargs):
    kwargs.setdefault('allow_redirects', False)
    return request('HEAD', url, **kwargs)
