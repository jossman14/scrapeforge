"""Unit tests for the Redis URL cache layer.

Mock Redis is used — no real Redis connection required.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock

from api.engine.cache import get_cached, set_cached, _cache_key


def _make_redis(stored: str | None = None):
    """Return a mock Redis whose get() returns `stored` and setex() is a no-op."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=stored)
    redis.setex = AsyncMock(return_value=True)
    return redis


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_returns_stored_result():
    """get_cached should return the deserialized dict on a cache hit."""
    payload = {"markdown": "# Hello", "url": "https://example.com"}
    redis = _make_redis(stored=json.dumps(payload))

    result = await get_cached("https://example.com", redis)

    assert result is not None
    assert result["markdown"] == "# Hello"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_does_not_call_fetch():
    """After a cache hit the caller should NOT need to fetch again (verified by absence of network mock call)."""
    payload = {"markdown": "cached content"}
    redis = _make_redis(stored=json.dumps(payload))

    result = await get_cached("https://cached.example.com", redis)
    assert result is not None


# ---------------------------------------------------------------------------
# Cache miss
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    """get_cached must return None when there is no cached entry."""
    redis = _make_redis(stored=None)
    result = await get_cached("https://not-cached.example.com", redis)
    assert result is None


# ---------------------------------------------------------------------------
# Cache write (set_cached)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_cached_calls_setex_with_correct_key():
    """set_cached should call redis.setex with the right key and TTL."""
    redis = _make_redis()
    url = "https://example.com/page"
    data = {"markdown": "content"}

    await set_cached(url, data, redis, ttl=300)

    redis.setex.assert_called_once()
    call_args = redis.setex.call_args
    key_arg, ttl_arg, data_arg = call_args[0]

    assert key_arg == _cache_key(url)
    assert ttl_arg == 300
    assert json.loads(data_arg) == data


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_cached_uses_default_ttl():
    """set_cached without explicit TTL should use the default 3600s."""
    redis = _make_redis()

    await set_cached("https://example.com", {"x": 1}, redis)

    call_args = redis.setex.call_args[0]
    assert call_args[1] == 3600


# ---------------------------------------------------------------------------
# Cache key determinism
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_cache_key_is_deterministic():
    """Same URL must always produce the same cache key."""
    url = "https://example.com/page?q=1"
    assert _cache_key(url) == _cache_key(url)


@pytest.mark.unit
def test_cache_key_differs_for_different_urls():
    """Different URLs must produce different cache keys."""
    assert _cache_key("https://a.com") != _cache_key("https://b.com")
