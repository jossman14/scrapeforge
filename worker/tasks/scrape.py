"""arq task: execute a scrape job.

Fetches a URL, converts to markdown, persists result in Postgres.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def task_scrape(ctx: dict, job_id: str) -> None:
    """arq entrypoint for scrape jobs."""
    session: AsyncSession = ctx["session"]
    redis = ctx["redis"]

    from api.db.models import Job, JobResult
    from api.engine.fetch import FetchOptions, fetch_with_fallback
    from api.engine.convert import html_to_markdown, extract_links
    from api.engine.cache import get_cached, set_cached
    from api.engine.robots import is_allowed

    result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
    job: Job | None = result.scalar_one_or_none()
    if job is None:
        log.error("task_scrape: job not found id=%s", job_id)
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.worker_id = ctx.get("job_id", "unknown")
    await session.commit()

    url = job.url or ""
    options = job.options or {}

    try:
        # Cache check
        cached = await get_cached(url, redis)
        if cached:
            log.info("task_scrape: cache_hit job=%s url=%s", job_id, url)
            await _save_result(session, job, url, cached.get("fetch_strategy", "static"), cached)
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return

        # robots.txt check
        if options.get("respect_robots_txt", True):
            allowed = await is_allowed(url, redis)
            if not allowed:
                log.info("task_scrape: skipped disallowed_by_robots url=%s", url)
                job.status = "failed"
                job.error_message = f"Skipped: disallowed by robots.txt for {url}"
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return

        opts = FetchOptions(
            wait_for=options.get("wait_for", 0),
            timeout=options.get("timeout", 30),
        )
        fetch_result = await fetch_with_fallback(url, opts)

        data: dict = {"url": url, "fetch_strategy": fetch_result.fetch_strategy}
        formats = set(options.get("formats", ["markdown"]))

        if "markdown" in formats:
            data["markdown"] = html_to_markdown(
                fetch_result.html, url, options.get("only_main_content", True)
            )
        if "html" in formats or "rawHtml" in formats:
            data["rawHtml"] = fetch_result.html

        if "links" in formats:
            data["links"] = extract_links(fetch_result.html, url)

        await set_cached(url, data, redis, ttl=options.get("cache_ttl", 3600))
        await _save_result(session, job, url, fetch_result.fetch_strategy, data, fetch_result.status_code)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("task_scrape: completed job=%s tier=%s", job_id, fetch_result.fetch_strategy)

    except Exception as exc:
        log.error("task_scrape: failed job=%s error=%s", job_id, exc)
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()


async def _save_result(session, job, url, strategy, data, status_code=200):
    from api.db.models import JobResult
    jr = JobResult(
        job_id=job.id,
        url=url,
        status_code=status_code,
        markdown=data.get("markdown"),
        html=data.get("html"),
        raw_html=data.get("rawHtml"),
        links={"urls": data.get("links", [])},
        fetch_strategy=strategy,
    )
    session.add(jr)
    await session.flush()
