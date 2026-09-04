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

from api.engine.safe_http import MAX_RESPONSE_BYTES, safe_get
from api.engine.url_validator import BlockedURLError, validate_url, validate_url_sync

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
    """3-tier fallback chain. Returns the first successful result.

    A URL rejected by the SSRF guard (at any tier, including on a redirect hop)
    aborts the whole chain — it must never be retried with a different engine.
    """
    opts = options or FetchOptions()
    try:
        return await _fetch_chain(url, opts)
    except BlockedURLError as exc:
        log.warning("fetch_blocked url=%s reason=%s", url, exc)
        return FetchResult(error=str(exc), is_blocked=True, fetch_strategy="blocked")


async def _fetch_chain(url: str, opts: FetchOptions) -> FetchResult:
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
    """Tier 1 — sync guarded fetch, so we run it in a thread."""
    try:
        result = await asyncio.to_thread(_scrapling_static, url, opts)
        return result
    except BlockedURLError:
        raise  # SSRF guard — must abort the chain, never fall through to tier 2
    except Exception as exc:
        log.warning("static_fetch_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_js_required=True)


def _stealth_headers(url: str, opts: FetchOptions) -> dict[str, str]:
    """Scrapling's browser-like headers, without ceding transport control."""
    headers: dict[str, str] = {"User-Agent": opts.user_agent}
    try:
        from scrapling.engines.toolbelt import generate_convincing_referer, generate_headers
        headers.update(generate_headers(browser_mode=False))
        headers["referer"] = generate_convincing_referer(url)
    except Exception as exc:  # scrapling missing or API changed — headers are optional
        log.debug("stealth_headers_unavailable url=%s error=%s", url, exc)
    return headers


def _scrapling_static(url: str, opts: FetchOptions) -> FetchResult:
    """Synchronous static fetch (runs in thread pool).

    Uses Scrapling's stealth header generation but performs the request through
    :func:`api.engine.safe_http.safe_get`, so the connection is DNS-pinned,
    every redirect hop is re-validated and the body is size-capped.  Handing the
    URL to Scrapling's own httpx client would follow redirects unchecked.
    """
    return _httpx_fallback_static(url, opts)


def _httpx_fallback_static(url: str, opts: FetchOptions) -> FetchResult:
    """Guarded httpx GET: validated + pinned + redirect-checked + size-capped."""
    try:
        resp = safe_get(
            url,
            headers=_stealth_headers(url, opts),
            timeout=opts.timeout,
            max_bytes=MAX_RESPONSE_BYTES,
        )
    except BlockedURLError:
        raise
    except Exception as exc:
        return FetchResult(error=str(exc), is_js_required=True)

    html = resp.text
    if resp.truncated:
        log.warning("static_response_truncated url=%s limit=%d", url, MAX_RESPONSE_BYTES)
    return FetchResult(
        html=html,
        status_code=resp.status_code,
        is_js_required=_looks_js_heavy(html),
        is_blocked=_looks_blocked(html, resp.status_code),
    )


async def _fetch_stealthy(url: str, opts: FetchOptions) -> FetchResult:
    """Scrapling StealthyFetcher — sync, run in thread."""
    try:
        result = await asyncio.to_thread(_scrapling_stealthy, url, opts)
        return result
    except BlockedURLError:
        raise
    except Exception as exc:
        log.warning("stealthy_fetch_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_blocked=True)


def _scrapling_stealthy(url: str, opts: FetchOptions) -> FetchResult:
    """Synchronous StealthyFetcher call.

    Tier 2 drives a real browser, so we cannot pin its socket or intercept its
    redirects.  We therefore validate before handing the URL over and validate
    the browser's *final* URL afterwards, so content fetched from an internal
    service is discarded instead of being persisted and returned.
    """
    validate_url_sync(url)

    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        log.warning("StealthyFetcher not available, skipping tier 2")
        return FetchResult(is_blocked=True)

    try:
        fetcher = StealthyFetcher(auto_match=True)
        page = fetcher.fetch(url, timeout=opts.timeout * 1000)
    except Exception as exc:
        log.warning("stealthy_error url=%s error=%s", url, exc)
        return FetchResult(error=str(exc), is_blocked=True)

    final_url = str(getattr(page, "url", "") or url)
    if final_url != url:
        validate_url_sync(final_url)  # raises BlockedURLError -> chain aborts

    html = (page.html_content or "")[:MAX_RESPONSE_BYTES]
    status = page.status or 200
    return FetchResult(html=html, status_code=status, is_blocked=_looks_blocked(html, status))


async def _fetch_playwright(url: str, opts: FetchOptions) -> FetchResult:
    """Call the Playwright microservice for JS-heavy pages."""
    # The microservice endpoint itself is operator-configured, but the target
    # URL is attacker-influenced — validate it before it leaves this process.
    await validate_url(url)

    payload = {"url": url}
    if opts.wait_for:
        payload["waitFor"] = opts.wait_for

    try:
        async with httpx.AsyncClient(timeout=opts.timeout + 15) as client:
            resp = await client.post(_PLAYWRIGHT_URL, json=payload)
            resp.raise_for_status()
            html = (resp.json().get("html", "") or "")[:MAX_RESPONSE_BYTES]
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
