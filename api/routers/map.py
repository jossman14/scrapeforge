"""POST /v1/map — fast URL discovery via sitemap.xml + link extraction.

No content scraping — only discovers URLs. Returns synchronously.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, Field

from api.auth.rate_limit import check_rate_limit
from api.db.models import ApiKey
from api.engine.fetch import FetchOptions, _fetch_static
from api.engine.convert import extract_links

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["map"])


class MapRequest(BaseModel):
    url: AnyHttpUrl
    limit: int = Field(default=500, ge=1, le=5000)
    include_sitemap: bool = Field(default=True, alias="includeSitemap")

    model_config = {"populate_by_name": True}


class MapResponse(BaseModel):
    success: bool = True
    urls: list[str]
    meta: dict = {}


@router.post("/map", response_model=MapResponse)
async def map_site(
    body: MapRequest,
    api_key: ApiKey = Depends(check_rate_limit),
) -> MapResponse:
    seed_url = str(body.url)
    collected: set[str] = set()

    # Step 1: Parse sitemap.xml
    if body.include_sitemap:
        sitemap_urls = await _parse_sitemap(seed_url)
        collected.update(sitemap_urls)
        log.info("map_sitemap url=%s found=%d", seed_url, len(sitemap_urls))

    # Step 2: Shallow link extraction from the seed page
    opts = FetchOptions(timeout=15)
    try:
        result = await _fetch_static(seed_url, opts)
        if result.html:
            page_links = extract_links(result.html, seed_url)
            # Filter to same domain unless allow_external
            base_domain = urlparse(seed_url).netloc
            same_domain = [u for u in page_links if urlparse(u).netloc == base_domain]
            collected.update(same_domain)
            log.info("map_link_extract url=%s found=%d", seed_url, len(same_domain))
    except Exception as exc:
        log.warning("map_link_extract_error url=%s error=%s", seed_url, exc)

    urls = sorted(collected)[: body.limit]
    return MapResponse(success=True, urls=urls, meta={"total": len(urls)})


async def _parse_sitemap(base_url: str) -> list[str]:
    """Parse sitemap.xml and return all <loc> URLs."""
    parsed = urlparse(base_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(sitemap_url, headers={"User-Agent": "ScrapeForge/1.0"})
            if resp.status_code != 200:
                return []

            root = ElementTree.fromstring(resp.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            urls = [loc.text for loc in root.findall(".//sm:loc", ns) if loc.text]
            return urls
    except Exception as exc:
        log.debug("sitemap_parse_error url=%s error=%s", sitemap_url, exc)
        return []
