"""GET /v1/jobs/{jobId} — async job status and results."""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.api_key import require_api_key
from api.db.models import ApiKey, Job, JobResult
from api.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["jobs"])


class JobResultOut(BaseModel):
    url: str
    status_code: int | None = None
    markdown: str | None = None
    html: str | None = None
    links: list[str] | None = None
    fetch_strategy: str | None = None
    extracted_data: dict | None = None

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    success: bool = True
    data: dict
    meta: dict = {}


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: uuid.UUID,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> JobStatusResponse:
    result = await session.execute(
        select(Job)
        .options(selectinload(Job.results))
        .where(Job.id == job_id, Job.api_key_id == api_key.id)
    )
    job: Job | None = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    results_out = []
    for r in job.results:
        links = r.links.get("urls", []) if r.links else []
        results_out.append({
            "url": r.url,
            "status_code": r.status_code,
            "markdown": r.markdown,
            "html": r.html,
            "links": links,
            "fetch_strategy": r.fetch_strategy,
            "extracted_data": r.extracted_data,
        })

    data = {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "url": job.url,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "results": results_out,
    }
    meta = {"result_count": len(results_out)}

    return JobStatusResponse(success=True, data=data, meta=meta)
