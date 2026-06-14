"""3-tier fetch fallback engine.

Tier 1: Scrapling.Fetcher  — static, fast, requests-based
Tier 2: StealthyFetcher   — anti-bot, Cloudflare bypass (sync → run in thread)
Tier 3: Playwright service — last resort for JS-heavy / SPA (HTTP call)

The tier used is logged and stored in job_results.fetch_strategy.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

_PLAYWRIGHT_URL = os.environ.get("PLAYWRIGHT_URL", "http://playwright:3000/render")
_TIMEOUT = 30


@dataclass
class FetchResult:
    html: str = ""
    status_code: int = 0
    fetch_strategy: str = "static"
    is_js_required: bool = False
    is_blocked: bool = False
    error: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.html.strip()) < 200


@dataclass
class FetchOptions:
    wait_for: int = 0  # ms to wait before extracting (Playwright only)
    timeout: int = _TIMEOUT
    user_agent: str = "ScrapeForge/1.0 (+https://github.com/scrapeforge)"
    proxy: str = ""


async def fetch_with_fallback(url: str, options: FetchOptions | None = None) -> FetchResult:
    """3-tier fallback chain. Returns the first successful result."""
    opts = options or FetchOptions()

    # Tier 1: Scrapling static Fetcher
    result = await _fetch_static(url, opts)
    log.info("fetch_tier=static url=%s status=%d empty=%s", url, result.status_code, result.is_empty)

    if not result.is_empty and not result.is_js_required and not result.is_blocked:
        result.fetch_strategy = "static"
        return result

    # Tier 2: StealthyFetcher (anti-bot / Cloudflare bypass)
    log.info("fetch_tier=stealthy url=%s reason=empty=%s js=%s blocked=%s",
             url, result.is_empty, result.is_js_required, result.is_blocked)
    result2 = await _fetch_stealthy(url, opts)
    if not result2.is_empty and not result2.is_blocked:
        result2.fetch_strategy = "stealthy"
        log.info("fetch_tier=stealthy url=%s status=%d SUCCESS", url, result2.status_code)
        return result2

    # Tier 3: Playwright service
    log.info("fetch_tier=playwright url=%s reason=stealthy_blocked=%s", url, result2.is_blocked)
    result3 = await _fetch_playwright(url, opts)
    result3.fetch_strategy = "playwright"
    log.info("fetch_tier=playwright url=%s status=%d empty=%s", url, result3.status_code, result3.is_empty)
    return result3


async def _fetch_static(url: str, opts: FetchOptions) -> FetchResult:
    """Scrapling Fetcher — sync, so we run it in a thread."""
    try:
        result = await asyncio.to_thread(_scrapling_static, url, opts)
        return result
    except Exception as exc:
        log.warning("static_fetch_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_js_required=True)


def _scrapling_static(url: str, opts: FetchOptions) -> FetchResult:
    """Synchronous Scrapling Fetcher call (runs in thread pool)."""
    try:
        from scrapling.fetchers import Fetcher
        fetcher = Fetcher(auto_match=True)
        page = fetcher.get(url, timeout=opts.timeout, stealthy_headers=True)
        html = page.html_content or ""
        status = page.status or 200

        is_js = _looks_js_heavy(html)
        is_blocked = _looks_blocked(html, status)

        return FetchResult(
            html=html,
            status_code=status,
            is_js_required=is_js,
            is_blocked=is_blocked,
        )
    except ImportError:
        return _httpx_fallback_static(url, opts)
    except Exception as exc:
        log.warning("scrapling_static_error url=%s error=%s", url, exc)
        return _httpx_fallback_static(url, opts)


def _httpx_fallback_static(url: str, opts: FetchOptions) -> FetchResult:
    """httpx-based fallback when scrapling is unavailable."""
    import httpx as _httpx
    try:
        with _httpx.Client(timeout=opts.timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": opts.user_agent})
            html = resp.text
            is_js = _looks_js_heavy(html)
            is_blocked = _looks_blocked(html, resp.status_code)
            return FetchResult(
                html=html,
                status_code=resp.status_code,
                is_js_required=is_js,
                is_blocked=is_blocked,
            )
    except Exception as exc:
        return FetchResult(error=str(exc), is_js_required=True)


async def _fetch_stealthy(url: str, opts: FetchOptions) -> FetchResult:
    """Scrapling StealthyFetcher — sync, run in thread."""
    try:
        result = await asyncio.to_thread(_scrapling_stealthy, url, opts)
        return result
    except Exception as exc:
        log.warning("stealthy_fetch_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_blocked=True)


def _scrapling_stealthy(url: str, opts: FetchOptions) -> FetchResult:
    """Synchronous StealthyFetcher call."""
    try:
        from scrapling.fetchers import StealthyFetcher
        fetcher = StealthyFetcher(auto_match=True)
        page = fetcher.fetch(url, timeout=opts.timeout * 1000)
        html = page.html_content or ""
        status = page.status or 200
        is_blocked = _looks_blocked(html, status)
        return FetchResult(html=html, status_code=status, is_blocked=is_blocked)
    except ImportError:
        log.warning("StealthyFetcher not available, skipping tier 2")
        return FetchResult(is_blocked=True)
    except Exception as exc:
        log.warning("stealthy_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_blocked=True)


async def _fetch_playwright(url: str, opts: FetchOptions) -> FetchResult:
    """Call the Playwright microservice for JS-heavy pages."""
    payload = {"url": url}
    if opts.wait_for:
        payload["waitFor"] = opts.wait_for

    try:
        async with httpx.AsyncClient(timeout=opts.timeout + 15) as client:
            resp = await client.post(_PLAYWRIGHT_URL, json=payload)
            resp.raise_for_status()
            html = resp.json().get("html", "")
            return FetchResult(html=html, status_code=200)
    except Exception as exc:
        log.error("playwright_fetch_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc))


def _looks_js_heavy(html: str) -> bool:
    """Heuristic: page body has almost no text but has <script> tags."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = " ".join(text.split())
    scripts = html.lower().count("<script")
    return len(text) < 300 and scripts > 3


def _looks_blocked(html: str, status: int) -> bool:
    """Heuristic: Cloudflare / bot detection challenge page."""
    if status in (403, 429, 503):
        return True
    lower = html.lower()
    indicators = ["cloudflare", "cf-browser-verification", "just a moment", "enable javascript"]
    return any(ind in lower for ind in indicators)
