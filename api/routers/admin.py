"""Admin dashboard — queue monitoring and API key management.

Protected by ADMIN_KEY cookie. Set ADMIN_KEY env var to enable.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import hash_key
from api.db.models import ApiKey, Job
from api.db.session import get_session
from api.deps import get_redis_pool

log = logging.getLogger(__name__)

ADMIN_KEY: str = os.environ.get("ADMIN_KEY", "")
_START_TIME: float = time.monotonic()

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter(prefix="/admin", tags=["admin"])


def _is_authenticated(request: Request) -> bool:
    if not ADMIN_KEY:
        return False
    session_val = request.cookies.get("admin_session", "")
    return hmac.compare_digest(session_val, ADMIN_KEY)


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.1f} GB"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin/login.html", {"request": request})


@router.post("/login")
async def login_submit(request: Request, admin_key: str = Form(...)):
    if not ADMIN_KEY or admin_key != ADMIN_KEY:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Invalid admin key."},
            status_code=401,
        )
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie(
        "admin_session",
        value=ADMIN_KEY,
        httponly=True,
        samesite="strict",
        max_age=86400 * 7,
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("admin_session")
    return resp


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin/dashboard.html", {"request": request})


# ---------------------------------------------------------------------------
# JSON API — used by dashboard polling JS
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def api_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(tz=timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # Redis health
    redis_info: dict[str, Any] = {"connected": False, "used_memory": None}
    try:
        r = get_redis_pool()
        mem = await r.info("memory")
        redis_info = {
            "connected": True,
            "used_memory": _format_bytes(mem.get("used_memory", 0)),
        }
    except Exception:
        pass

    # Postgres health
    pg_ok = False
    try:
        await session.execute(text("SELECT 1"))
        pg_ok = True
    except Exception:
        pass

    # Worker count — arq workers write heartbeat keys
    worker_count = 0
    try:
        r = get_redis_pool()
        keys = await r.keys("arq:health-check:*")
        worker_count = len(keys)
    except Exception:
        pass

    uptime_seconds = int(time.monotonic() - _START_TIME)

    # Job status totals
    status_counts: dict[str, int] = {}
    try:
        rows = await session.execute(
            select(Job.status, func.count().label("cnt")).group_by(Job.status)
        )
        for row in rows:
            status_counts[row.status] = row.cnt
    except Exception:
        pass

    # 24h aggregates
    completed_24h = 0
    failed_24h = 0
    avg_duration: float | None = None
    jobs_per_minute: float | None = None
    try:
        row24 = await session.execute(
            select(
                func.count().filter(Job.status == "completed").label("completed"),
                func.count().filter(Job.status == "failed").label("failed"),
            ).where(Job.created_at >= cutoff_24h)
        )
        r24 = row24.one()
        completed_24h = r24.completed or 0
        failed_24h = r24.failed or 0

        dur_row = await session.execute(
            select(
                func.avg(
                    func.extract("epoch", Job.completed_at)
                    - func.extract("epoch", Job.started_at)
                ).label("avg_dur")
            ).where(
                Job.status == "completed",
                Job.created_at >= cutoff_24h,
                Job.started_at.isnot(None),
                Job.completed_at.isnot(None),
            )
        )
        avg_dur_val = dur_row.scalar_one_or_none()
        if avg_dur_val is not None:
            avg_duration = round(float(avg_dur_val), 1)

        cutoff_60m = now - timedelta(minutes=60)
        jpm_row = await session.execute(
            select(func.count()).where(Job.created_at >= cutoff_60m)
        )
        total_60m = jpm_row.scalar_one() or 0
        jobs_per_minute = round(total_60m / 60, 2)
    except Exception as exc:
        log.warning("admin stats query error: %s", exc)

    return JSONResponse({
        "health": {
            "redis": redis_info,
            "postgres": {"connected": pg_ok},
            "workers": worker_count,
            "uptime_seconds": uptime_seconds,
        },
        "queue": {
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "completed": status_counts.get("completed", 0),
            "failed": status_counts.get("failed", 0),
            "completed_24h": completed_24h,
            "failed_24h": failed_24h,
            "avg_duration_seconds": avg_duration,
            "jobs_per_minute": jobs_per_minute,
        },
    })


@router.get("/api/jobs")
async def api_jobs(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await session.execute(
        select(Job).order_by(Job.created_at.desc()).limit(20)
    )
    jobs = result.scalars().all()

    rows = []
    for job in jobs:
        duration = None
        if job.started_at and job.completed_at:
            delta = job.completed_at - job.started_at
            duration = round(delta.total_seconds(), 1)

        url = job.url or ""
        rows.append({
            "id": str(job.id),
            "type": job.type,
            "url": (url[:72] + "...") if len(url) > 75 else url,
            "status": job.status,
            "duration_seconds": duration,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "error_message": job.error_message,
        })

    return JSONResponse(rows)


@router.get("/api/keys")
async def api_keys_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.is_active.is_(True))
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    rows = [
        {
            "id": str(k.id),
            "name": k.name or "(unnamed)",
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "rate_limit_per_minute": k.rate_limit_per_minute,
        }
        for k in keys
    ]
    return JSONResponse(rows)


class CreateKeyRequest(BaseModel):
    name: str = ""
    rate_limit_per_minute: int = 60


@router.post("/api/keys")
async def api_keys_create(
    request: Request,
    body: CreateKeyRequest,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw_key = "sf_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    new_key = ApiKey(
        key_hash=key_hash,
        name=body.name or None,
        rate_limit_per_minute=max(1, min(body.rate_limit_per_minute, 10000)),
    )
    session.add(new_key)
    await session.commit()
    await session.refresh(new_key)

    return JSONResponse(
        {
            "id": str(new_key.id),
            "name": new_key.name,
            "key": raw_key,
            "rate_limit_per_minute": new_key.rate_limit_per_minute,
        },
        status_code=201,
    )


@router.delete("/api/keys/{key_id}")
async def api_keys_revoke(
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()

    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")

    key.is_active = False
    await session.commit()

    return JSONResponse({"status": "revoked", "id": str(key_id)})
