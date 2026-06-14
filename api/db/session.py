"""Async database session factory for ScrapeForge."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/scrapeforge",
)

_echo_sql = os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG"

engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # discard stale connections before checkout
    echo=_echo_sql,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # avoid implicit lazy-loads after commit
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a transactional async session per request."""
    async with async_session_factory() as session:
        yield session
