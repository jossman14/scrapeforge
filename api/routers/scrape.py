"""POST /v1/scrape — synchronous single-URL scrape.

Returns markdown, html, rawHtml, links, and/or screenshot inline.
No job queue for single-URL scrapes — response is immediate.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.rate_limit import check_rate_limit
from api.db.models import ApiKey, JobResult, Job
from api.db.session import get_session
from api.deps import get_redis
from api.engine.cache import get_cached, set_cached
from api.engine.convert import extract_links, html_to_markdown
from api.engine.fetch import FetchOptions, fetch_with_fallback
from api.engine.robots import is_allowed

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["scrape"])


class ScrapeRequest(BaseModel):
    url: AnyHttpUrl
    formats: list[str] = Field(
        default=["markdown"],
        description="Output formats: markdown, html, rawHtml, links, screenshot, json",
    )
    only_main_content: bool = Field(default=True, alias="onlyMainContent")
    wait_for: int = Field(default=0, alias="waitFor", ge=0, le=30000)
    timeout: int = Field(default=30, ge=5, le=120)
    respect_robots_txt: bool = Field(default=True, alias="respectRobotsTxt")
    cache_ttl: int = Field(default=3600, alias="cacheTtl", ge=0)

    model_config = {"populate_by_name": True}


class ScrapeResponse(BaseModel):
    success: bool = True
    data: dict
    meta: dict = Field(default_factory=dict)


@router.post("/scrape", response_model=ScrapeResponse)
async def scrape(
    body: ScrapeRequest,
    request: Request,
    api_key: ApiKey = Depends(check_rate_limit),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> ScrapeResponse:
    url = str(body.url)

    # Cache check
    if body.cache_ttl > 0:
        cached = await get_cached(url, redis)
        if cached is not None:
            return ScrapeResponse(success=True, data=cached, meta={"cached": True})

    # robots.txt check
    if body.respect_robots_txt:
        allowed = await is_allowed(url, redis)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"URL blocked by robots.txt: {url}",
            )

    # Fetch
    opts = FetchOptions(wait_for=body.wait_for, timeout=body.timeout)
    result = await fetch_with_fallback(url, opts)

    if not result.html and result.error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch URL: {result.error}",
        )

    # Build response data from requested formats
    data: dict = {"url": url, "fetch_strategy": result.fetch_strategy}

    formats = {f.lower() for f in body.formats}

    if "markdown" in formats:
        data["markdown"] = html_to_markdown(result.html, url, body.only_main_content)

    if "html" in formats:
        data["html"] = html_to_markdown(result.html, url, only_main_content=False)

    if "rawhtml" in formats or "rawHtml" in formats:
        data["rawHtml"] = result.html

    if "links" in formats:
        data["links"] = extract_links(result.html, url)

    if "screenshot" in formats:
        data["screenshot"] = None  # screenshots only available via Playwright job

    # Persist result and cache
    await _persist_result(session, url, result, data, api_key)
    if body.cache_ttl > 0:
        await set_cached(url, data, redis, ttl=body.cache_ttl)

    return ScrapeResponse(success=True, data=data, meta={"cached": False})


async def _persist_result(session, url, fetch_result, data, api_key):
    """Save the scrape result to Postgres for audit/history."""
    try:
        job = Job(
            type="scrape",
            status="completed",
            url=url,
            options={},
            api_key_id=api_key.id,
        )
        session.add(job)
        await session.flush()

        jr = JobResult(
            job_id=job.id,
            url=url,
            status_code=fetch_result.status_code,
            markdown=data.get("markdown"),
            html=data.get("html"),
            raw_html=data.get("rawHtml"),
            links={"urls": data.get("links", [])},
            fetch_strategy=fetch_result.fetch_strategy,
        )
        session.add(jr)
        await session.commit()
    except Exception as exc:
        log.error("persist_result_error url=%s error=%s", url, exc)
        await session.rollback()
