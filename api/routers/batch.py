"""POST /v1/batch/scrape — enqueue multiple URLs as a single batch job.

Each URL becomes a child job tracked under a parent batch job.
Returns a jobId; use GET /v1/jobs/{jobId} for progress.
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

router = APIRouter(prefix="/v1/batch", tags=["batch"])


class BatchScrapeRequest(BaseModel):
    urls: list[AnyHttpUrl] = Field(..., min_length=1, max_length=100)
    formats: list[str] = Field(default=["markdown"])
    only_main_content: bool = Field(default=True, alias="onlyMainContent")

    model_config = {"populate_by_name": True}


class BatchScrapeResponse(BaseModel):
    success: bool = True
    jobId: str
    message: str = "Batch scrape job queued. Use GET /v1/jobs/{jobId} to check progress."
    url_count: int


@router.post("/scrape", response_model=BatchScrapeResponse, status_code=status.HTTP_202_ACCEPTED)
async def batch_scrape(
    body: BatchScrapeRequest,
    api_key: ApiKey = Depends(check_rate_limit),
    session: AsyncSession = Depends(get_session),
) -> BatchScrapeResponse:
    urls = [str(u) for u in body.urls]
    for u in urls:
        try:
            await validate_url(u)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid URL '{u}': {exc}",
            )
    options = {
        "urls": urls,
        "formats": body.formats,
        "only_main_content": body.only_main_content,
    }
    job = await enqueue_job(
        job_type="batch",
        url=urls[0],  # primary URL for display
        options=options,
        api_key_id=api_key.id,
        session=session,
    )
    return BatchScrapeResponse(success=True, jobId=str(job.id), url_count=len(urls))
