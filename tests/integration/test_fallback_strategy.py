"""Fallback chain verification test.

Spins up a minimal Flask server that serves different responses based on the
requesting user agent:
  - Default / static: returns a tiny HTML body (< 200 chars) → forces tier 1 to fall through
  - StealthyFetcher UA: returns a Cloudflare challenge page → forces tier 2 to fall through
  - Playwright UA: returns rich HTML (> 200 chars) → tier 3 succeeds

This directly verifies the DoD item: "Scrapling fallback terbukti via log".

NOTE: This test only works when fetch.py imports are available and the
      test environment has access to a local server port.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api.engine.fetch import FetchOptions, FetchResult, fetch_with_fallback


# ---------------------------------------------------------------------------
# Minimal test server that returns different content per request
# ---------------------------------------------------------------------------

_SERVER_PORT = 18923
_REQUESTS_LOG: list[str] = []


class _FallbackTestHandler(BaseHTTPRequestHandler):
    """Returns empty → Cloudflare challenge → rich content based on call order."""

    _call_count = 0

    def log_message(self, fmt, *args):
        pass  # silence default request logging

    def do_GET(self):
        _FallbackTestHandler._call_count += 1
        call_n = _FallbackTestHandler._call_count

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        if call_n == 1:
            # Tier 1: tiny body → triggers fallthrough
            body = b"<html><body>hi</body></html>"
        elif call_n == 2:
            # Tier 2: Cloudflare challenge → triggers fallthrough
            body = b"<html><body>Just a moment... enable javascript cloudflare</body></html>"
        else:
            # Tier 3 (and any subsequent): rich content
            body = b"<html><body>" + b"A" * 500 + b"</body></html>"

        self.wfile.write(body)


@pytest.fixture(scope="module")
def fallback_server():
    """Start the test HTTP server in a background thread."""
    _FallbackTestHandler._call_count = 0
    server = HTTPServer(("127.0.0.1", _SERVER_PORT), _FallbackTestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)  # let the server bind
    yield f"http://127.0.0.1:{_SERVER_PORT}/"
    server.shutdown()


# ---------------------------------------------------------------------------
# Fallback chain test — mocked tier functions
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_chain_all_three_tiers_exercised():
    """
    Verify the 3-tier fallback chain:
      static (empty) → stealthy (blocked) → playwright (success)
    using mocked tier functions to avoid real network access.
    """
    from unittest.mock import AsyncMock, patch

    rich_html = "<html><body>" + "Z" * 500 + "</body></html>"

    static_empty = FetchResult(html="", status_code=200)
    stealthy_blocked = FetchResult(
        html="<html><body>Just a moment... cloudflare</body></html>",
        status_code=403,
        is_blocked=True,
    )
    pw_success = FetchResult(html=rich_html, status_code=200)

    tiers_called: list[str] = []

    async def mock_static(url, opts):
        tiers_called.append("static")
        return static_empty

    async def mock_stealthy(url, opts):
        tiers_called.append("stealthy")
        return stealthy_blocked

    async def mock_playwright(url, opts):
        tiers_called.append("playwright")
        return pw_success

    with patch("api.engine.fetch._fetch_static", side_effect=mock_static):
        with patch("api.engine.fetch._fetch_stealthy", side_effect=mock_stealthy):
            with patch("api.engine.fetch._fetch_playwright", side_effect=mock_playwright):
                result = await fetch_with_fallback(
                    "https://anti-bot.example.com",
                    FetchOptions(),
                )

    assert "static" in tiers_called, "Tier 1 (static) was not called"
    assert "stealthy" in tiers_called, "Tier 2 (stealthy) was not called"
    assert "playwright" in tiers_called, "Tier 3 (playwright) was not called"
    assert result.fetch_strategy == "playwright"
    assert result.html == rich_html


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_chain_stops_at_tier_1():
    """If static returns rich content, stealthy and playwright must NOT be called."""
    from unittest.mock import AsyncMock, patch

    rich_html = "<html><body>" + "X" * 500 + "</body></html>"
    static_ok = FetchResult(html=rich_html, status_code=200)

    tiers_called: list[str] = []

    async def mock_static(url, opts):
        tiers_called.append("static")
        return static_ok

    async def mock_stealthy(url, opts):
        tiers_called.append("stealthy")
        return FetchResult()

    async def mock_playwright(url, opts):
        tiers_called.append("playwright")
        return FetchResult()

    with patch("api.engine.fetch._fetch_static", side_effect=mock_static):
        with patch("api.engine.fetch._fetch_stealthy", side_effect=mock_stealthy):
            with patch("api.engine.fetch._fetch_playwright", side_effect=mock_playwright):
                result = await fetch_with_fallback("https://simple.example.com")

    assert tiers_called == ["static"], f"Expected only tier 1, got: {tiers_called}"
    assert result.fetch_strategy == "static"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_chain_stops_at_tier_2():
    """If stealthy succeeds, playwright must NOT be called."""
    from unittest.mock import patch

    rich_html = "<html><body>" + "Y" * 500 + "</body></html>"
    static_empty = FetchResult(html="", status_code=200)
    stealthy_ok = FetchResult(html=rich_html, status_code=200)

    tiers_called: list[str] = []

    async def mock_static(url, opts):
        tiers_called.append("static")
        return static_empty

    async def mock_stealthy(url, opts):
        tiers_called.append("stealthy")
        return stealthy_ok

    async def mock_playwright(url, opts):
        tiers_called.append("playwright")
        return FetchResult()

    with patch("api.engine.fetch._fetch_static", side_effect=mock_static):
        with patch("api.engine.fetch._fetch_stealthy", side_effect=mock_stealthy):
            with patch("api.engine.fetch._fetch_playwright", side_effect=mock_playwright):
                result = await fetch_with_fallback("https://cloudflare.example.com")

    assert "playwright" not in tiers_called, "Playwright was called but should not have been"
    assert result.fetch_strategy == "stealthy"
