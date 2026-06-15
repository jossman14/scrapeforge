"""POST /v1/crawl — async recursive crawl from a seed URL.

Creates a queued job and returns the jobId. The arq worker executes
BFS crawling using the crawl_frontier table.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.rate_limit import check_rate_limit
from api.db.models import ApiKey
from api.db.session import get_session
from api.engine.url_validator import validate_url
from api.queue.producer import enqueue_job

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["crawl"])


class CrawlRequest(BaseModel):
    url: AnyHttpUrl
    max_depth: int = Field(default=3, alias="maxDepth", ge=1, le=10)
    limit: int = Field(default=100, ge=1, le=1000)
    include_paths: list[str] = Field(default=[], alias="includePaths")
    exclude_paths: list[str] = Field(default=[], alias="excludePaths")
    allow_external: bool = Field(default=False, alias="allowExternal")
    respect_robots_txt: bool = Field(default=True, alias="respectRobotsTxt")

    model_config = {"populate_by_name": True}


class CrawlResponse(BaseModel):
    success: bool = True
    jobId: str
    message: str = "Crawl job queued. Use GET /v1/jobs/{jobId} to check status."


@router.post("/crawl", response_model=CrawlResponse, status_code=status.HTTP_202_ACCEPTED)
async def crawl(
    body: CrawlRequest,
    api_key: ApiKey = Depends(check_rate_limit),
    session: AsyncSession = Depends(get_session),
) -> CrawlResponse:
    try:
        await validate_url(str(body.url))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    options = {
        "max_depth": body.max_depth,
        "limit": body.limit,
        "include_paths": body.include_paths,
        "exclude_paths": body.exclude_paths,
        "allow_external": body.allow_external,
        "respect_robots_txt": body.respect_robots_txt,
    }
    job = await enqueue_job(
        job_type="crawl",
        url=str(body.url),
        options=options,
        api_key_id=api_key.id,
        session=session,
    )
    return CrawlResponse(success=True, jobId=str(job.id))
