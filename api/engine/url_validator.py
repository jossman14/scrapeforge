"""SSRF protection: validate *and pin* URLs before any fetch operation.

Security model
--------------
1. Scheme allowlist — only ``http`` and ``https``.
2. Hostname denylist — cloud metadata names.
3. DNS resolution — **every** address returned by ``getaddrinfo`` must be a
   globally routable unicast address.  Anything private, loopback, link-local,
   reserved, unspecified, multicast, CGNAT/shared, benchmarking or otherwise
   non-global is rejected.  IPv4-mapped IPv6 addresses (``::ffff:10.0.0.1``)
   are unwrapped first so they cannot smuggle a private v4 address past the
   check.  Unparseable literals fail closed.
4. **Pinning** — the validated address is returned to the caller so the actual
   TCP connection is made to the exact IP that was checked.  This closes the
   DNS-rebinding / TOCTOU window between "validate" and "connect".  Callers
   keep the original ``Host`` header and TLS SNI so name-based vhosts and
   certificate verification continue to work normally.

``BlockedURLError`` subclasses ``ValueError`` so all existing
``except ValueError`` handlers (routers return HTTP 422) keep working.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Hostnames that must never be fetched even if DNS says otherwise.
_BLOCKED_HOSTS: set[str] = {
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "instance-data",
    "169.254.169.254",
}


class BlockedURLError(ValueError):
    """Raised when a URL is not safe to fetch (SSRF guard)."""


@dataclass(frozen=True)
class ValidatedTarget:
    """A URL that passed validation, together with the IP to connect to."""

    url: str
    scheme: str
    hostname: str      # for Host header / TLS SNI (no brackets, no port)
    port: int
    ip: str            # validated, pinned address — connect to *this*
    family: int        # socket.AF_INET / socket.AF_INET6

    @property
    def host_header(self) -> str:
        """``Host:`` header value preserving the original name and port."""
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        if self.port == _DEFAULT_PORTS.get(self.scheme):
            return host
        return f"{host}:{self.port}"


def is_blocked_ip(addr: str) -> bool:
    """Return True if ``addr`` must not be connected to.

    Uses the stdlib address classification instead of a hand-maintained CIDR
    list, so ``0.0.0.0``, ``0``, ``::``, CGNAT (100.64.0.0/10), 240.0.0.0/4 and
    friends are all covered.  IPv4-mapped IPv6 is unwrapped first.
    Unparseable input fails closed (blocked).
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        log.warning("ssrf_block unparseable_address=%r", addr)
        return True

    # ::ffff:10.0.0.1 must be judged as 10.0.0.1, not as an IPv6 address.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    ):
        return True

    # Belt and braces: catches ranges the flags above miss, e.g. CGNAT
    # 100.64.0.0/10 (is_private is False for it on some Python versions).
    return not ip.is_global


# Backwards-compatible alias (previously the only helper in this module).
def _is_private_ip(addr: str) -> bool:
    return is_blocked_ip(addr)


def _blocked_hostname(hostname: str) -> bool:
    return hostname.lower().rstrip(".") in _BLOCKED_HOSTS


def validate_url_sync(url: str) -> ValidatedTarget:
    """Validate ``url`` and return the pinned target. Raises BlockedURLError.

    Synchronous variant — safe to call from worker threads (Scrapling runs in
    ``asyncio.to_thread``).
    """
    parsed = urlparse(url)

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedURLError(
            f"Scheme '{parsed.scheme}' not allowed. Only http and https are permitted."
        )

    hostname = parsed.hostname
    if not hostname:
        raise BlockedURLError("URL has no hostname.")
    hostname = hostname.rstrip(".")
    if not hostname:
        raise BlockedURLError("URL has no hostname.")

    if _blocked_hostname(hostname):
        log.warning("ssrf_block hostname=%s url=%s", hostname, url)
        raise BlockedURLError(f"Host '{hostname}' is not allowed.")

    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:  # out-of-range port in the URL
        raise BlockedURLError(f"Invalid port in URL: {exc}") from exc

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Could not resolve hostname '{hostname}': {exc}") from exc

    if not infos:
        raise BlockedURLError(f"Hostname '{hostname}' did not resolve to any address.")

    pinned: tuple[int, str] | None = None
    for family, _type, _proto, _canon, sockaddr in infos:
        resolved_ip = sockaddr[0]
        if is_blocked_ip(resolved_ip):
            log.warning("ssrf_block hostname=%s resolved=%s url=%s", hostname, resolved_ip, url)
            raise BlockedURLError(
                f"URL resolves to a private or reserved IP address '{resolved_ip}', "
                "which is not allowed."
            )
        if pinned is None:
            pinned = (family, resolved_ip)

    assert pinned is not None  # non-empty infos + no rejection
    family, ip = pinned
    return ValidatedTarget(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        ip=ip,
        family=family,
    )


async def validate_url(url: str) -> ValidatedTarget:
    """Async wrapper around :func:`validate_url_sync`.

    Raises ``BlockedURLError`` (a ``ValueError``) if the URL is not safe.
    DNS resolution is offloaded to a thread so the event loop is not blocked.
    """
    return await asyncio.to_thread(validate_url_sync, url)
