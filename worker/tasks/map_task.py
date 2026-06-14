"""arq task: async map job (URL discovery)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def task_map(ctx: dict, job_id: str) -> None:
    """arq entrypoint for map jobs."""
    session: AsyncSession = ctx["session"]
    redis = ctx["redis"]

    from api.db.models import Job, JobResult
    from api.routers.map import _parse_sitemap
    from api.engine.fetch import FetchOptions, _fetch_static
    from api.engine.convert import extract_links
    from urllib.parse import urlparse

    result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
    job: Job | None = result.scalar_one_or_none()
    if not job:
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    url = job.url or ""
    options = job.options or {}

    try:
        collected: set[str] = set()

        sitemap_urls = await _parse_sitemap(url)
        collected.update(sitemap_urls)

        opts = FetchOptions(timeout=15)
        static_result = await _fetch_static(url, opts)
        if static_result.html:
            base_domain = urlparse(url).netloc
            links = extract_links(static_result.html, url)
            same_domain = [u for u in links if urlparse(u).netloc == base_domain]
            collected.update(same_domain)

        urls = sorted(collected)[: options.get("limit", 500)]

        jr = JobResult(
            job_id=job.id,
            url=url,
            status_code=200,
            links={"urls": urls},
            fetch_strategy="static",
        )
        session.add(jr)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("task_map: completed job=%s urls=%d", job_id, len(urls))

    except Exception as exc:
        log.error("task_map: failed job=%s error=%s", job_id, exc)
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
