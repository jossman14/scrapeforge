"""SSRF protection: validate URLs before any fetch operation.

Raises ValueError if the URL:
- is not HTTP or HTTPS
- resolves to a private, loopback, or link-local IP
- is in the explicit blocklist
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_BLOCKED_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP/Azure metadata
    ipaddress.ip_network("100.64.0.0/10"),   # shared address space
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_BLOCKED_HOSTS: set[str] = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
}


def _is_private_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
        for net in _BLOCKED_RANGES:
            if ip in net:
                return True
        return False
    except ValueError:
        return False


async def validate_url(url: str) -> None:
    """Raise ValueError if the URL is not safe to fetch.

    Checks:
    1. Scheme must be http or https.
    2. Hostname must not be in the explicit blocklist.
    3. Resolved IP must not be in private/loopback/link-local ranges.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme '{parsed.scheme}' not allowed. Only http and https are permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname.")

    if hostname.lower() in _BLOCKED_HOSTS:
        log.warning("ssrf_block hostname=%s url=%s", hostname, url)
        raise ValueError(f"Host '{hostname}' is not allowed.")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{hostname}': {exc}") from exc

    for info in infos:
        resolved_ip = info[4][0]
        if _is_private_ip(resolved_ip):
            log.warning("ssrf_block hostname=%s resolved=%s url=%s", hostname, resolved_ip, url)
            raise ValueError(
                f"URL resolves to a private or reserved IP address '{resolved_ip}', "
                "which is not allowed."
            )
