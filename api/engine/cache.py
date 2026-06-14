"""Redis + Postgres URL result cache.

Before any fetch, check Redis for a cached result.
After fetch, write to Redis and optionally to Postgres url_cache table.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # 1 hour


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _cache_key(url: str) -> str:
    return f"cache:{_url_hash(url)}"


async def get_cached(url: str, redis: aioredis.Redis) -> dict | None:
    """Return cached scrape result or None."""
    key = _cache_key(url)
    raw = await redis.get(key)
    if raw:
        log.info("cache_hit url=%s", url)
        return json.loads(raw)
    return None


async def set_cached(
    url: str,
    result: dict[str, Any],
    redis: aioredis.Redis,
    ttl: int = _DEFAULT_TTL,
) -> None:
    """Store result in Redis with given TTL."""
    key = _cache_key(url)
    await redis.setex(key, ttl, json.dumps(result))
    log.debug("cache_set url=%s ttl=%d", url, ttl)
