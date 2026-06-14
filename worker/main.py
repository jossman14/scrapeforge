"""ScrapeForge arq worker entry point.

arq uses WorkerSettings to discover tasks and configure the worker.
All Scrapling calls run in-process (ADR-002: in-process within arq worker).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import redis.asyncio as aioredis
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from worker.tasks.scrape import task_scrape
from worker.tasks.crawl import task_crawl
from worker.tasks.map_task import task_map
from worker.tasks.extract_task import task_extract
from worker.tasks.batch_task import task_batch

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/scrapeforge",
)
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")


def _parse_redis_settings(url: str) -> RedisSettings:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "redis",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "0"),
    )


async def startup(ctx: dict) -> None:
    """Initialize DB session and Redis for each worker context."""
    engine = create_async_engine(_DATABASE_URL, pool_size=3, max_overflow=5)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_factory()

    redis = aioredis.from_url(_REDIS_URL, decode_responses=True)

    ctx["engine"] = engine
    ctx["session"] = session
    ctx["redis"] = redis
    log.info("worker_startup: connections ready")


async def shutdown(ctx: dict) -> None:
    """Clean up on worker shutdown."""
    await ctx["session"].close()
    await ctx["engine"].dispose()
    await ctx["redis"].aclose()
    log.info("worker_shutdown: connections closed")


class WorkerSettings:
    """arq WorkerSettings — consumed by the arq CLI."""

    redis_settings = _parse_redis_settings(_REDIS_URL)

    # All task functions must be listed here
    functions = [task_scrape, task_crawl, task_map, task_extract, task_batch]

    on_startup = startup
    on_shutdown = shutdown

    max_jobs = 5
    job_timeout = 300  # 5 minutes max per job
    keep_result = 3600  # keep job result in Redis for 1 hour
