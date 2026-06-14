"""arq task: execute a batch scrape job (multiple URLs)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_MAX_CONCURRENT = 5  # limit concurrent fetches per batch


async def task_batch(ctx: dict, job_id: str) -> None:
    """arq entrypoint for batch scrape jobs."""
    session: AsyncSession = ctx["session"]
    redis = ctx["redis"]

    from api.db.models import Job, JobResult
    from api.engine.fetch import FetchOptions, fetch_with_fallback
    from api.engine.convert import html_to_markdown, extract_links
    from api.engine.cache import get_cached, set_cached
    from api.engine.robots import is_allowed

    result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
    job: Job | None = result.scalar_one_or_none()
    if not job:
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    options = job.options or {}
    urls: list[str] = options.get("urls", [])
    formats = set(options.get("formats", ["markdown"]))
    only_main = options.get("only_main_content", True)

    async def scrape_one(url: str) -> None:
        try:
            cached = await get_cached(url, redis)
            if cached:
                log.info("batch_cache_hit url=%s", url)
                jr = JobResult(
                    job_id=job.id,
                    url=url,
                    markdown=cached.get("markdown"),
                    fetch_strategy=cached.get("fetch_strategy", "static"),
                )
                session.add(jr)
                return

            if options.get("respect_robots_txt", True):
                if not await is_allowed(url, redis):
                    log.info("batch_skipped_robots url=%s", url)
                    return

            opts = FetchOptions(timeout=20)
            fetch_result = await fetch_with_fallback(url, opts)

            data: dict = {}
            if "markdown" in formats:
                data["markdown"] = html_to_markdown(fetch_result.html, url, only_main)
            if "links" in formats:
                data["links"] = extract_links(fetch_result.html, url)

            await set_cached(url, {**data, "fetch_strategy": fetch_result.fetch_strategy}, redis)

            jr = JobResult(
                job_id=job.id,
                url=url,
                status_code=fetch_result.status_code,
                markdown=data.get("markdown"),
                links={"urls": data.get("links", [])},
                fetch_strategy=fetch_result.fetch_strategy,
            )
            session.add(jr)
            log.debug("batch_scraped url=%s tier=%s", url, fetch_result.fetch_strategy)

        except Exception as exc:
            log.error("batch_url_error url=%s error=%s", url, exc)

    try:
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

        async def bounded(url: str) -> None:
            async with semaphore:
                await scrape_one(url)

        await asyncio.gather(*[bounded(u) for u in urls])
        await session.commit()

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("task_batch: completed job=%s urls=%d", job_id, len(urls))

    except Exception as exc:
        log.error("task_batch: failed job=%s error=%s", job_id, exc)
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
