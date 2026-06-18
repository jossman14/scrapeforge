"""Unit tests for SSRF-protection URL validator.

validate_url performs synchronous DNS resolution, so we patch socket.getaddrinfo
to control what IP addresses are returned without real DNS calls.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from api.engine.url_validator import validate_url


def _make_addrinfo(ip: str):
    """Return a minimal getaddrinfo result for the given IP."""
    return [(None, None, None, None, (ip, 0))]


# ---------------------------------------------------------------------------
# Private / reserved IPs must be blocked
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_private_ip_10_range():
    """10.x.x.x (RFC 1918) must be blocked."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("10.0.0.1")):
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_url("http://internal.example.com/secret")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_loopback():
    """127.x.x.x (loopback) must be blocked."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("127.0.0.1")):
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_url("http://localhost/admin")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_link_local_metadata():
    """169.254.x.x (AWS/GCP metadata service) must be blocked."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("169.254.169.254")):
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_url("http://169.254.169.254/latest/meta-data/")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_rfc1918_172_range():
    """172.16–31.x.x (RFC 1918) must be blocked."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("172.16.0.1")):
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_url("http://corp.internal/")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_rfc1918_192_range():
    """192.168.x.x (RFC 1918) must be blocked."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("192.168.1.1")):
        with pytest.raises(ValueError, match="private or reserved"):
            await validate_url("http://router.local/")


# ---------------------------------------------------------------------------
# Explicitly blocked hostnames
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_gcp_metadata_hostname():
    """metadata.google.internal must be blocked by hostname before DNS."""
    with pytest.raises(ValueError, match="not allowed"):
        await validate_url("http://metadata.google.internal/computeMetadata/v1/")


# ---------------------------------------------------------------------------
# Public URLs must be allowed
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_allows_public_url():
    """A URL resolving to a public IP must pass validation."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("93.184.216.34")):
        await validate_url("https://example.com")  # should not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_allows_http_scheme():
    """http:// scheme must be accepted."""
    with patch("socket.getaddrinfo", return_value=_make_addrinfo("93.184.216.34")):
        await validate_url("http://example.com")  # should not raise


# ---------------------------------------------------------------------------
# Non-HTTP schemes must be rejected
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_ftp_scheme():
    """ftp:// must be rejected regardless of IP."""
    with pytest.raises(ValueError, match="Scheme"):
        await validate_url("ftp://files.example.com/data.csv")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_file_scheme():
    """file:// must be rejected (local file read)."""
    with pytest.raises(ValueError, match="Scheme"):
        await validate_url("file:///etc/passwd")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejects_javascript_scheme():
    """javascript: scheme must be rejected."""
    with pytest.raises(ValueError, match="Scheme"):
        await validate_url("javascript:alert(1)")
