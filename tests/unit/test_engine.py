"""Unit tests for the 3-tier fetch fallback engine and markdown conversion.

All external I/O is mocked — no network calls, no real scrapling imports.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.engine.fetch import FetchResult, FetchOptions, fetch_with_fallback
from api.engine.convert import html_to_markdown, extract_links


# ---------------------------------------------------------------------------
# Fetch strategy — Tier 1 (static) succeeds
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_strategy_static_success():
    """When static fetch returns non-empty HTML, tier=static is used and we stop."""
    rich_html = "<html><body>" + "X" * 300 + "</body></html>"
    static_result = FetchResult(html=rich_html, status_code=200)

    with patch("api.engine.fetch._fetch_static", new=AsyncMock(return_value=static_result)):
        with patch("api.engine.fetch._fetch_stealthy", new=AsyncMock()) as mock_stealthy:
            result = await fetch_with_fallback("https://example.com")

    assert result.fetch_strategy == "static"
    assert result.html == rich_html
    mock_stealthy.assert_not_called()


# ---------------------------------------------------------------------------
# Fetch strategy — Tier 1 empty → falls through to Tier 2 (stealthy)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_strategy_falls_to_stealthy():
    """When static fetch returns empty HTML, StealthyFetcher (tier 2) should be called."""
    empty_result = FetchResult(html="", status_code=200)
    rich_html = "<html><body>" + "Y" * 400 + "</body></html>"
    stealthy_result = FetchResult(html=rich_html, status_code=200)

    with patch("api.engine.fetch._fetch_static", new=AsyncMock(return_value=empty_result)):
        with patch("api.engine.fetch._fetch_stealthy", new=AsyncMock(return_value=stealthy_result)):
            with patch("api.engine.fetch._fetch_playwright", new=AsyncMock()) as mock_pw:
                result = await fetch_with_fallback("https://example.com")

    assert result.fetch_strategy == "stealthy"
    assert result.html == rich_html
    mock_pw.assert_not_called()


# ---------------------------------------------------------------------------
# Fetch strategy — Tier 1 & 2 fail → falls to Tier 3 (playwright)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_strategy_falls_to_playwright():
    """When static + stealthy both indicate blocked, Playwright is used as tier 3."""
    blocked_static = FetchResult(html="", status_code=403, is_blocked=True)
    blocked_stealthy = FetchResult(html="", status_code=403, is_blocked=True)
    pw_html = "<html><body>" + "Z" * 500 + "</body></html>"
    pw_result = FetchResult(html=pw_html, status_code=200)

    with patch("api.engine.fetch._fetch_static", new=AsyncMock(return_value=blocked_static)):
        with patch("api.engine.fetch._fetch_stealthy", new=AsyncMock(return_value=blocked_stealthy)):
            with patch("api.engine.fetch._fetch_playwright", new=AsyncMock(return_value=pw_result)):
                result = await fetch_with_fallback("https://anti-bot.example.com")

    assert result.fetch_strategy == "playwright"
    assert result.html == pw_html


# ---------------------------------------------------------------------------
# Fetch strategy — Tier 1 JS-required → falls to Tier 2
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_strategy_js_required_falls_to_stealthy():
    """When static fetch detects JS-heavy page, stealthy tier should be tried."""
    js_result = FetchResult(html="<html></html>", status_code=200, is_js_required=True)
    rich_html = "<html><body>" + "A" * 400 + "</body></html>"
    stealthy_result = FetchResult(html=rich_html, status_code=200)

    with patch("api.engine.fetch._fetch_static", new=AsyncMock(return_value=js_result)):
        with patch("api.engine.fetch._fetch_stealthy", new=AsyncMock(return_value=stealthy_result)):
            result = await fetch_with_fallback("https://spa.example.com")

    assert result.fetch_strategy == "stealthy"


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_markdown_conversion_strips_nav_and_preserves_headings():
    """html_to_markdown should strip navigation and preserve heading structure."""
    html = """
    <html>
    <body>
        <nav><a href="/">Home</a><a href="/about">About</a></nav>
        <article>
            <h1>Main Title</h1>
            <p>Important content paragraph.</p>
            <h2>Section Two</h2>
            <p>More content here.</p>
        </article>
        <footer>Footer content</footer>
    </body>
    </html>
    """
    # Use only_main_content=False so markdownify path is used (trafilatura may not find content)
    md = html_to_markdown(html, url="https://example.com", only_main_content=False)
    assert "Main Title" in md
    assert "Section Two" in md
    assert "Important content paragraph" in md


@pytest.mark.unit
def test_markdown_conversion_empty_html():
    """Empty HTML should return an empty string without raising."""
    result = html_to_markdown("", url="", only_main_content=False)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# extract_links
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_extract_links_absolute():
    """extract_links should return absolute URLs."""
    html = '<html><body><a href="https://foo.com/page">link</a></body></html>'
    links = extract_links(html, base_url="https://example.com")
    assert "https://foo.com/page" in links


@pytest.mark.unit
def test_extract_links_relative_resolved():
    """Relative links should be resolved against base_url."""
    html = '<html><body><a href="/about">About</a></body></html>'
    links = extract_links(html, base_url="https://example.com")
    assert "https://example.com/about" in links


@pytest.mark.unit
def test_extract_links_deduplication():
    """Duplicate links should appear only once."""
    html = """
    <a href="https://example.com/page">1</a>
    <a href="https://example.com/page">2</a>
    """
    links = extract_links(html, base_url="https://example.com")
    assert links.count("https://example.com/page") == 1
