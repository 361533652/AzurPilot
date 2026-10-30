"""Dependency-light HTTP settings and optional runtime transport for deployment.

Importing this module requires only the standard library. Once runtime
packages are installed, downloads use the HTTP/3-first client. Before that,
bootstrap uses the existing Requests transport and browser download headers.
"""

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = 'AzurPilot/2.1'
BROWSER_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
)
DOWNLOAD_HEADERS = {
    'User-Agent': BROWSER_USER_AGENT,
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
SERVICE_DOMAINS = ('azurpilot.com', 'nanoda.work')


def is_service_url(url):
    """Match the service roots and their subdomains, without suffix lookalikes."""
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    return parsed.scheme in ('http', 'https') and any(
        host == domain or host.endswith('.' + domain) for domain in SERVICE_DOMAINS
    )


def _transport():
    try:
        from module.base import http
        return http
    except ModuleNotFoundError as error:
        # Only a missing optional transport permits bootstrap fallback. A broken
        # application import must remain visible instead of silently downgrading.
        if error.name not in ('module', 'module.base', 'module.base.http', 'niquests'):
            raise
        import requests
        return requests


def Session(*, download=False, retries=0):
    transport = _transport()
    if transport.__name__ == 'requests':
        from requests.adapters import HTTPAdapter
        session = transport.Session()
        session.headers.update(DOWNLOAD_HEADERS if download else {'User-Agent': USER_AGENT})
        session.mount('http://', HTTPAdapter(max_retries=retries))
        session.mount('https://', HTTPAdapter(max_retries=retries))
        return session
    return transport.Session(download=download, retries=retries)


def __getattr__(name):
    if name in ('RequestException', 'exceptions'):
        return getattr(_transport(), name)
    raise AttributeError(name)


def request(method, url, *, download=False, **kwargs):
    with Session(download=download) as session:
        return session.request(method, url, **kwargs)


def get(url, **kwargs):
    kwargs.setdefault('allow_redirects', True)
    return request('GET', url, **kwargs)


def head(url, **kwargs):
    kwargs.setdefault('allow_redirects', False)
    return request('HEAD', url, **kwargs)


def download_file(url, target, *, timeout=(5, 30)):
    """Atomically populate an upstream download cache without an Origin header."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size:
        return str(target)
    partial = None
    try:
        with get(url, download=True, stream=True, timeout=timeout,
                 headers={'Accept-Encoding': 'identity'}) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.part', delete=False) as output:
                partial = Path(output.name)
                for chunk in response.iter_content(65536):
                    output.write(chunk)
            expected = response.headers.get('Content-Length')
            if expected is not None and partial.stat().st_size != int(expected):
                raise ValueError('Incomplete download')
        os.replace(partial, target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return str(target)


# Git's libcurl exposes HTTP/2, but has no HTTP/3 option in http.version.
# URL-specific settings take precedence over the generic third-party mirror UA.
GIT_HTTP_OPTIONS = ' '.join(
    f'-c "http.https://{host}/.version=HTTP/2" '
    f'-c "http.https://{host}/.userAgent={BROWSER_USER_AGENT}"'
    for domain in SERVICE_DOMAINS for host in (domain, '*.' + domain)
)
