"""Per-API-key sliding window rate limiter backed by Redis.

Uses a ZADD/ZRANGEBYSCORE sorted set per key, where the score is the
Unix timestamp of each request. Expired entries are pruned on every check.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis

from api.auth.api_key import require_api_key
from api.db.models import ApiKey
from api.deps import get_redis

log = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


async def check_rate_limit(
    api_key: ApiKey = Depends(require_api_key),
    redis: Redis = Depends(get_redis),
) -> ApiKey:
    """Enforce sliding-window rate limit; raises 429 if exceeded."""
    rk = f"ratelimit:{api_key.id}"
    now = time.time()
    window_start = now - _WINDOW_SECONDS

    pipe = redis.pipeline()
    pipe.zremrangebyscore(rk, 0, window_start)
    pipe.zadd(rk, {str(now): now})
    pipe.zcard(rk)
    pipe.expire(rk, _WINDOW_SECONDS * 2)
    results = await pipe.execute()

    count = results[2]
    limit = api_key.rate_limit_per_minute

    if count > limit:
        log.warning("rate_limit_exceeded key=%s count=%d limit=%d", api_key.id, count, limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests/minute.",
            headers={"Retry-After": str(_WINDOW_SECONDS)},
        )

    return api_key
