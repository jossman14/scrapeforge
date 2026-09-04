"""robots.txt compliance checker with Redis caching (1h TTL).

Fetches and parses robots.txt per domain; results are cached in Redis to
avoid repeated fetches on every scrape request.
"""

from __future__ import annotations

import logging
import urllib.robotparser
from urllib.parse import urlparse

import redis.asyncio as aioredis

from api.engine.safe_http import safe_get_async

log = logging.getLogger(__name__)

_TTL = 3600  # 1 hour
_MAX_ROBOTS_BYTES = 512 * 1024  # robots.txt is never legitimately larger


def _domain_key(url: str) -> str:
    parsed = urlparse(url)
    return f"robots:{parsed.scheme}://{parsed.netloc}"


def _robots_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


async def is_allowed(url: str, redis: aioredis.Redis, user_agent: str = "*") -> bool:
    """Return True if the URL is allowed by robots.txt, False if disallowed."""
    cache_key = _domain_key(url)
    cached = await redis.get(cache_key)

    if cached is not None:
        robots_text = cached
    else:
        robots_text = await _fetch_robots(url)
        await redis.setex(cache_key, _TTL, robots_text or "")

    if not robots_text:
        return True  # no robots.txt → allow

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_text.splitlines())
    allowed = rp.can_fetch(user_agent, url)

    if not allowed:
        log.info("robots_disallowed url=%s", url)

    return allowed


async def _fetch_robots(url: str) -> str:
    robots_url = _robots_url(url)
    try:
        # Guarded fetch: robots.txt is requested for attacker-supplied hosts on
        # every crawl hop, so it is an SSRF sink in its own right.
        resp = await safe_get_async(
            robots_url,
            headers={"User-Agent": "ScrapeForge/1.0"},
            timeout=10.0,
            max_bytes=_MAX_ROBOTS_BYTES,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        log.debug("robots_fetch_error url=%s error=%s", robots_url, exc)
    return ""
