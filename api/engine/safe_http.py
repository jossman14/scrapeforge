"""SSRF-safe HTTP client.

Every request made through this module is:

* **validated** — :func:`api.engine.url_validator.validate_url_sync` runs on the
  initial URL *and on every redirect hop*;
* **pinned** — the TCP connection is made to the exact IP that was validated
  (URL host rewritten to the IP, original ``Host`` header + TLS SNI preserved),
  which closes the DNS-rebinding / TOCTOU window;
* **redirect-bounded** — ``follow_redirects`` is disabled at the transport
  level and hops are walked manually, at most :data:`MAX_REDIRECTS` of them;
* **size-bounded** — the body is streamed and truncated at
  :data:`MAX_RESPONSE_BYTES` so a hostile origin cannot fill Postgres.

TLS note: the certificate is verified against ``sni_hostname`` (the real
hostname), *not* against the pinned IP, so pinning does not weaken TLS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from api.engine.url_validator import BlockedURLError, validate_url_sync

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass
class SafeResponse:
    """Result of a guarded fetch."""

    url: str  # final URL after validated redirects
    status_code: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    redirects: int = 0
    encoding: str = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")


def _prepare(url: str, headers: dict[str, str] | None):
    """Validate + pin ``url``; return (connect_url, headers, extensions)."""
    target = validate_url_sync(url)

    connect_url = httpx.URL(url).copy_with(host=target.ip)
    req_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "host"}
    req_headers["Host"] = target.host_header

    # Keep TLS handshake + certificate verification bound to the real hostname.
    extensions = {"sni_hostname": target.hostname} if target.scheme == "https" else {}

    log.debug("safe_http_pin url=%s host=%s ip=%s", url, target.hostname, target.ip)
    return connect_url, req_headers, extensions


def _next_hop(current_url: str, location: str) -> str:
    nxt = urljoin(current_url, location)
    if urlparse(nxt).scheme.lower() not in ("http", "https"):
        raise BlockedURLError(f"Redirect to disallowed scheme: {location!r}")
    return nxt


def _finish(current_url: str, resp: httpx.Response, body: bytes, truncated: bool, hops: int) -> SafeResponse:
    return SafeResponse(
        url=current_url,
        status_code=resp.status_code,
        content=body,
        headers={k.lower(): v for k, v in resp.headers.items()},
        truncated=truncated,
        redirects=hops,
        encoding=resp.charset_encoding or "utf-8",
    )


def safe_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> SafeResponse:
    """Blocking guarded GET. Raises ``BlockedURLError`` for unsafe targets."""
    current = url
    for hop in range(max_redirects + 1):
        connect_url, req_headers, extensions = _prepare(current, headers)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream(
                "GET", connect_url, headers=req_headers, extensions=extensions
            ) as resp:
                location = (
                    resp.headers.get("location")
                    if resp.status_code in _REDIRECT_STATUSES
                    else None
                )
                if location is None:
                    body, truncated = _read_capped(resp, max_bytes, current)
                    return _finish(current, resp, body, truncated, hop)

        current = _next_hop(current, location)
        log.info("safe_http_redirect hop=%d to=%s", hop + 1, current)

    raise BlockedURLError(f"Too many redirects (> {max_redirects}) starting at {url}")


async def safe_get_async(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> SafeResponse:
    """Async guarded GET. Raises ``BlockedURLError`` for unsafe targets."""
    current = url
    for hop in range(max_redirects + 1):
        connect_url, req_headers, extensions = _prepare(current, headers)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "GET", connect_url, headers=req_headers, extensions=extensions
            ) as resp:
                location = (
                    resp.headers.get("location")
                    if resp.status_code in _REDIRECT_STATUSES
                    else None
                )
                if location is None:
                    body, truncated = await _read_capped_async(resp, max_bytes, current)
                    return _finish(current, resp, body, truncated, hop)

        current = _next_hop(current, location)
        log.info("safe_http_redirect hop=%d to=%s", hop + 1, current)

    raise BlockedURLError(f"Too many redirects (> {max_redirects}) starting at {url}")


def _truncate(chunks: list[bytes], total: int, chunk: bytes, max_bytes: int) -> None:
    keep = max_bytes - (total - len(chunk))
    if keep > 0:
        chunks.append(chunk[:keep])


def _read_capped(resp: httpx.Response, max_bytes: int, url: str) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            _truncate(chunks, total, chunk, max_bytes)
            log.warning("safe_http_truncated url=%s limit=%d", url, max_bytes)
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False


async def _read_capped_async(resp: httpx.Response, max_bytes: int, url: str) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            _truncate(chunks, total, chunk, max_bytes)
            log.warning("safe_http_truncated url=%s limit=%d", url, max_bytes)
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False
