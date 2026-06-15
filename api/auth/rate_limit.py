"""Per-API-key sliding window rate limiter backed by Redis.

Uses a Lua script to atomically: prune expired entries, add current request,
and read count — all in a single round-trip. This prevents the race condition
where concurrent requests could each observe count < limit and all proceed.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis

from api.auth.api_key import require_api_key
from api.db.models import ApiKey
from api.deps import get_redis

log = logging.getLogger(__name__)

_WINDOW_SECONDS = 60

# Atomic sliding-window check: prune, add, count, get oldest entry timestamp.
# Returns [count_after_add, oldest_score_or_empty_string]
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local expire_ttl = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
redis.call('ZADD', key, now, tostring(now))
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, expire_ttl)

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local oldest_score = oldest[2] or tostring(now)

return {count, oldest_score}
"""


async def check_rate_limit(
    api_key: ApiKey = Depends(require_api_key),
    redis: Redis = Depends(get_redis),
) -> ApiKey:
    """Enforce sliding-window rate limit; raises 429 if exceeded."""
    rk = f"ratelimit:{api_key.id}"
    now = time.time()
    window_start = now - _WINDOW_SECONDS

    results = await redis.eval(
        _RATE_LIMIT_LUA,
        1,
        rk,
        str(now),
        str(window_start),
        str(_WINDOW_SECONDS * 2),
    )

    count = int(results[0])
    limit = api_key.rate_limit_per_minute

    if count > limit:
        # Calculate accurate retry-after: when the oldest entry in the window expires
        oldest_score = float(results[1])
        retry_after = max(1, math.ceil(oldest_score + _WINDOW_SECONDS - now))

        log.warning("rate_limit_exceeded key=%s count=%d limit=%d", api_key.id, count, limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests/minute.",
            headers={"Retry-After": str(retry_after)},
        )

    return api_key
