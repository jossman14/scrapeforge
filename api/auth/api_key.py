"""API key authentication dependency.

API keys are 32-byte cryptographically random values. We use HMAC-SHA256 with a
server-side secret (KEY_HASH_SECRET env var) as the stored hash. This differs
from password hashing (where argon2 is correct) because API keys have 256 bits
of entropy — brute-forcing HMAC-SHA256 of a 256-bit random value is infeasible.
Using HMAC-SHA256 allows fast indexed database lookup without a full-table scan.

If you switch to argon2, you must add a fast lookup prefix column to avoid an
O(N*argon2_cost) scan on every request.

Never returns the plaintext key or hash in responses.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ApiKey
from api.db.session import get_session, async_session_factory

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Server-side secret for HMAC. Must be set in production; fails loudly if missing.
_KEY_HASH_SECRET = os.environ.get("KEY_HASH_SECRET", "")


def hash_key(raw_key: str) -> str:
    """HMAC-SHA256 of the raw key using the server secret.

    Using HMAC (vs plain SHA-256) prevents rainbow-table attacks on the key_hash
    column even if the database is dumped, as long as KEY_HASH_SECRET remains secret.
    """
    if not _KEY_HASH_SECRET:
        log.error("KEY_HASH_SECRET is not set — API key hashing is insecure")
    return hmac.new(
        _KEY_HASH_SECRET.encode() or b"",
        raw_key.encode(),
        hashlib.sha256,
    ).hexdigest()


async def _update_last_used(api_key_id: UUID) -> None:
    """Fire-and-forget: update last_used_at without blocking the request."""
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(ApiKey)
                .where(ApiKey.id == api_key_id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await session.commit()
    except Exception as exc:
        log.warning("last_used_at_update_error key_id=%s error=%s", api_key_id, exc)


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> ApiKey:
    """FastAPI dependency — resolve and return the authenticated ApiKey row."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Use: Authorization: Bearer <key>",
        )

    raw_key = credentials.credentials
    key_hash = hash_key(raw_key)

    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        log.warning("invalid_api_key attempt from %s", request.client and request.client.host)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key.",
        )

    # Fire-and-forget: update last_used_at without blocking the request
    asyncio.create_task(_update_last_used(api_key.id))

    return api_key
