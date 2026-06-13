import os
import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/scrapeforge"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
app = FastAPI(title="ScrapeForge API", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    errors = {}

    try:
        db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(db_url, timeout=5)
        await conn.close()
    except Exception as e:
        errors["database"] = str(e)

    try:
        r = aioredis.from_url(settings.redis_url, socket_timeout=5)
        await r.ping()
        await r.aclose()
    except Exception as e:
        errors["redis"] = str(e)

    if errors:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not_ready", "errors": errors})

    return {"status": "ready"}
