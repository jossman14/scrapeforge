"""arq task: BFS recursive crawl using crawl_frontier table."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def task_crawl(ctx: dict, job_id: str) -> None:
    """arq entrypoint for crawl jobs."""
    session: AsyncSession = ctx["session"]
    redis = ctx["redis"]

    from api.db.models import Job, CrawlFrontier, JobResult
    from api.engine.fetch import FetchOptions, fetch_with_fallback
    from api.engine.convert import html_to_markdown, extract_links
    from api.engine.robots import is_allowed
    from api.engine.url_validator import validate_url

    result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
    job: Job | None = result.scalar_one_or_none()
    if not job:
        log.error("task_crawl: job not found id=%s", job_id)
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    url = job.url or ""
    options = job.options or {}
    max_depth = options.get("max_depth", 3)
    limit = options.get("limit", 100)
    allow_external = options.get("allow_external", False)
    respect_robots = options.get("respect_robots_txt", True)
    include_paths = options.get("include_paths", [])
    exclude_paths = options.get("exclude_paths", [])
    base_domain = urlparse(url).netloc

    try:
        # Seed the frontier
        frontier_entry = CrawlFrontier(job_id=job.id, url=url, depth=0, status="pending")
        session.add(frontier_entry)
        await session.commit()

        visited: set[str] = set()
        processed = 0

        while processed < limit:
            # Claim next pending URL (BFS: lowest depth first)
            stmt = (
                select(CrawlFrontier)
                .where(
                    CrawlFrontier.job_id == job.id,
                    CrawlFrontier.status == "pending",
                )
                .order_by(CrawlFrontier.depth)
                .limit(1)
            )
            res = await session.execute(stmt)
            entry: CrawlFrontier | None = res.scalar_one_or_none()
            if not entry:
                break

            if entry.depth > max_depth:
                break

            entry.status = "processing"
            await session.commit()

            current_url = entry.url
            visited.add(current_url)

            # SSRF guard: frontier URLs come from attacker-controlled HTML and
            # allow_external disables the domain filter, so every URL is
            # re-validated here — before robots.txt and before the fetch.
            try:
                await validate_url(current_url)
            except ValueError as exc:
                log.warning("crawl_blocked_ssrf url=%s reason=%s", current_url, exc)
                entry.status = "skipped"
                entry.processed_at = datetime.now(timezone.utc)
                await session.commit()
                continue

            # robots check
            if respect_robots and not await is_allowed(current_url, redis):
                log.info("crawl_skipped disallowed url=%s", current_url)
                entry.status = "skipped"
                entry.processed_at = datetime.now(timezone.utc)
                await session.commit()
                continue

            opts = FetchOptions(timeout=20)
            fetch_result = await fetch_with_fallback(current_url, opts)

            # Save result
            jr = JobResult(
                job_id=job.id,
                url=current_url,
                status_code=fetch_result.status_code,
                markdown=html_to_markdown(fetch_result.html, current_url),
                fetch_strategy=fetch_result.fetch_strategy,
            )
            session.add(jr)

            entry.status = "done"
            entry.processed_at = datetime.now(timezone.utc)
            processed += 1

            # Discover new links
            new_links = extract_links(fetch_result.html, current_url)
            for link in new_links:
                if link in visited:
                    continue
                parsed = urlparse(link)
                if not allow_external and parsed.netloc != base_domain:
                    continue
                if include_paths and not any(p in parsed.path for p in include_paths):
                    continue
                if exclude_paths and any(p in parsed.path for p in exclude_paths):
                    continue
                # SSRF guard before the link ever enters the frontier.
                try:
                    await validate_url(link)
                except ValueError as exc:
                    log.warning("crawl_link_rejected url=%s reason=%s", link, exc)
                    continue
                # Use insert-or-ignore to respect UNIQUE constraint
                await session.execute(
                    insert(CrawlFrontier)
                    .values(job_id=job.id, url=link, depth=entry.depth + 1, status="pending")
                    .on_conflict_do_nothing(constraint="uq_crawl_frontier_job_id_url")
                )

            await session.commit()

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("task_crawl: completed job=%s pages=%d", job_id, processed)

    except Exception as exc:
        log.error("task_crawl: failed job=%s error=%s", job_id, exc)
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
