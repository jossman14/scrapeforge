"""Job queue producer using arq.

Enqueues a job to Redis and creates the Postgres job record atomically.
The arq worker picks it up and executes the corresponding task.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
import os
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Job

log = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    # Parse redis://host:port/db
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "redis",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "0"),
    )


async def enqueue_job(
    job_type: str,
    url: str | None,
    options: dict[str, Any],
    api_key_id: UUID | None,
    session: AsyncSession,
) -> Job:
    """Create a Postgres job record and enqueue an arq task. Returns the Job."""
    job = Job(
        type=job_type,
        status="queued",
        url=url,
        options=options,
        api_key_id=api_key_id,
    )
    session.add(job)
    await session.flush()  # get job.id before enqueue

    # Enqueue in arq — the task name matches the function in worker/main.py
    pool = await create_pool(_redis_settings())
    await pool.enqueue_job(f"task_{job_type}", str(job.id))
    await pool.aclose()

    await session.commit()
    log.info("job_enqueued type=%s id=%s", job_type, job.id)
    return job
