"""API key authentication dependency.

Validates the Authorization header against hashed keys in the database.
Never returns the plaintext key or hash in responses.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ApiKey
from api.db.session import get_session

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of the raw key (fast lookup; argon2 hash stored separately)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


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
    key_hash = _hash_key(raw_key)

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

    return api_key
