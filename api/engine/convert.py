"""HTML → Markdown conversion with boilerplate removal.

Uses trafilatura for main-content extraction, then markdownify
to produce clean Markdown. Falls back to raw markdownify if
trafilatura returns nothing.
"""

from __future__ import annotations

import logging

import markdownify
import trafilatura

log = logging.getLogger(__name__)


def html_to_markdown(html: str, url: str = "", only_main_content: bool = True) -> str:
    """Convert raw HTML to LLM-ready Markdown.

    When only_main_content is True, trafilatura strips nav/ads/boilerplate
    before conversion, preserving the main article body.
    """
    if only_main_content:
        extracted = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            output_format="markdown",
        )
        if extracted:
            log.debug("trafilatura extracted %d chars", len(extracted))
            return extracted

        log.debug("trafilatura returned empty, falling back to full markdownify")

    md = markdownify.markdownify(
        html,
        heading_style=markdownify.ATX,
        strip=["script", "style", "nav", "footer", "header", "aside"],
    )
    return md.strip()


def extract_links(html: str, base_url: str = "") -> list[str]:
    """Extract all href links from HTML."""
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.links: list[str] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag == "a":
                for attr, val in attrs:
                    if attr == "href" and val:
                        self.links.append(val)

    parser = LinkParser()
    parser.feed(html)

    from urllib.parse import urljoin
    resolved = []
    for link in parser.links:
        if link.startswith(("http://", "https://")):
            resolved.append(link)
        elif base_url and link.startswith("/"):
            resolved.append(urljoin(base_url, link))

    return list(dict.fromkeys(resolved))  # dedup, preserve order
