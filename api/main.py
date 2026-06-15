"""ScrapeForge API — FastAPI entry point.

Lifespan: initializes DB connection pool and Redis on startup.
Routes: /health, /ready, /metrics, and all /v1/* endpoints.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from api.db.session import engine
from api.deps import get_redis_pool
from api.routers import scrape, crawl, map as map_router, extract, batch, jobs
from api.routers import admin

# --- Structured logging setup ---
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup: initializing connections")
    # Warm up DB pool by running a simple query
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        log.info("startup: database pool ready")
    except Exception as exc:
        log.error("startup: database connection failed: %s", exc)

    # Warm up Redis
    try:
        r = get_redis_pool()
        await r.ping()
        log.info("startup: redis ready")
    except Exception as exc:
        log.error("startup: redis connection failed: %s", exc)

    yield

    log.info("shutdown: closing connections")
    await engine.dispose()
    r = get_redis_pool()
    await r.aclose()


app = FastAPI(
    title="ScrapeForge API",
    description="Self-hosted web-data API powered by Scrapling. Firecrawl-compatible.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# Static files for admin dashboard
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Routers
app.include_router(scrape.router)
app.include_router(crawl.router)
app.include_router(map_router.router)
app.include_router(extract.router)
app.include_router(batch.router)
app.include_router(jobs.router)
app.include_router(admin.router)


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "scrapeforge-api"}


@app.get("/ready", tags=["ops"])
async def ready():
    errors: dict = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as exc:
        errors["database"] = str(exc)

    try:
        r = get_redis_pool()
        await r.ping()
    except Exception as exc:
        errors["redis"] = str(exc)

    if errors:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "errors": errors},
        )

    return {"status": "ready"}
