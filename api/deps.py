"""Shared FastAPI dependencies: Redis, DB session."""

from __future__ import annotations

import os
from typing import AsyncGenerator

import redis.asyncio as aioredis

_redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
_redis_pool: aioredis.Redis | None = None


def get_redis_pool() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(_redis_url, decode_responses=True)
    return _redis_pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency: yields the shared Redis connection pool."""
    yield get_redis_pool()
