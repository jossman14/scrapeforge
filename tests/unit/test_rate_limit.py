"""Unit tests for the sliding-window rate limiter.

We mock Redis so no real Redis connection is required.
The Lua script is not executed — we simulate its return values directly.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import HTTPException

from api.auth.rate_limit import check_rate_limit
from api.db.models import ApiKey


def _make_api_key(rate_limit: int = 60) -> ApiKey:
    """Build a minimal ApiKey ORM object without touching the DB."""
    key = ApiKey.__new__(ApiKey)
    key.id = uuid.uuid4()
    key.rate_limit_per_minute = rate_limit
    key.is_active = True
    return key


def _make_redis(count: int, oldest_score: float = None):
    """Return a mock Redis that simulates the Lua script returning (count, oldest_score)."""
    import time
    score = oldest_score if oldest_score is not None else time.time()
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=[count, str(score)])
    return redis


# ---------------------------------------------------------------------------
# Requests within the limit pass
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_within_limit_passes():
    """60 requests within the 60 req/min limit should pass."""
    api_key = _make_api_key(rate_limit=60)
    redis = _make_redis(count=60)

    result = await check_rate_limit(api_key=api_key, redis=redis)
    assert result is api_key


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_first_request_passes():
    """The very first request (count=1) must always pass."""
    api_key = _make_api_key(rate_limit=60)
    redis = _make_redis(count=1)

    result = await check_rate_limit(api_key=api_key, redis=redis)
    assert result is api_key


# ---------------------------------------------------------------------------
# Exceeding the limit raises 429
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_exceeded_raises_429():
    """61st request against a 60 req/min limit must raise HTTP 429."""
    api_key = _make_api_key(rate_limit=60)
    redis = _make_redis(count=61)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(api_key=api_key, redis=redis)

    assert exc_info.value.status_code == 429


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_exceeded_includes_retry_after_header():
    """429 response must include a Retry-After header with a positive integer."""
    import time
    api_key = _make_api_key(rate_limit=10)
    oldest = time.time() - 10  # 10 seconds into the 60s window
    redis = _make_redis(count=11, oldest_score=oldest)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(api_key=api_key, redis=redis)

    headers = exc_info.value.headers or {}
    assert "Retry-After" in headers
    retry_after = int(headers["Retry-After"])
    assert retry_after >= 1


# ---------------------------------------------------------------------------
# Custom rate limits per key
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_custom_limit_respected():
    """A key with rate_limit_per_minute=10 should be blocked at count=11."""
    api_key = _make_api_key(rate_limit=10)
    redis = _make_redis(count=11)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(api_key=api_key, redis=redis)

    assert exc_info.value.status_code == 429


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_high_limit_key_passes_at_60():
    """A key with rate_limit_per_minute=100 should still pass at count=60."""
    api_key = _make_api_key(rate_limit=100)
    redis = _make_redis(count=60)

    result = await check_rate_limit(api_key=api_key, redis=redis)
    assert result is api_key
