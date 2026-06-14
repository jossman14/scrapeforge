"""Scrapling adaptive self-healing selector store.

Stores selector signatures per URL pattern in Redis so that on re-scrape
the stored selector is tried first; if it fails, fall back to CSS/auto-extraction
and update the stored signature.

This is the key differentiator vs Firecrawl — selectors survive DOM changes.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

_SELECTOR_TTL = 86400 * 7  # 7 days


def _selector_key(url: str) -> str:
    parsed = urlparse(url)
    # Key by domain + path prefix (first 2 segments) to group similar pages
    parts = parsed.path.strip("/").split("/")[:2]
    pattern = f"{parsed.netloc}/{'/'.join(parts)}"
    return f"selector:{pattern}"


async def get_stored_selector(url: str, redis: aioredis.Redis) -> str | None:
    """Retrieve a previously successful CSS selector for this URL pattern."""
    key = _selector_key(url)
    selector = await redis.get(key)
    if selector:
        log.debug("adaptive_selector_hit pattern=%s selector=%s", key, selector)
    return selector


async def store_selector(url: str, selector: str, redis: aioredis.Redis) -> None:
    """Persist a working selector signature for future reuse."""
    key = _selector_key(url)
    await redis.setex(key, _SELECTOR_TTL, selector)
    log.debug("adaptive_selector_stored pattern=%s selector=%s", key, selector)


async def clear_selector(url: str, redis: aioredis.Redis) -> None:
    """Invalidate a stored selector after it fails."""
    key = _selector_key(url)
    await redis.delete(key)
    log.debug("adaptive_selector_cleared pattern=%s", key)
