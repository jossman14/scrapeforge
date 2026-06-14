"""arq task: LLM structured extraction."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")


async def task_extract(ctx: dict, job_id: str) -> None:
    """arq entrypoint for extract jobs."""
    session: AsyncSession = ctx["session"]

    from api.db.models import Job, JobResult
    from api.engine.fetch import FetchOptions, fetch_with_fallback
    from api.engine.convert import html_to_markdown

    result = await session.execute(select(Job).where(Job.id == UUID(job_id)))
    job: Job | None = result.scalar_one_or_none()
    if not job:
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    url = job.url or ""
    options = job.options or {}

    try:
        if not _LLM_API_KEY:
            raise RuntimeError("LLM_API_KEY not configured")

        fetch_result = await fetch_with_fallback(url, FetchOptions(timeout=30))
        markdown = html_to_markdown(fetch_result.html, url, options.get("only_main_content", True))

        schema = options.get("schema", {})
        prompt = options.get("prompt", "")
        extracted = await _extract(markdown, schema, prompt)

        jr = JobResult(
            job_id=job.id,
            url=url,
            status_code=fetch_result.status_code,
            extracted_data=extracted,
            fetch_strategy=fetch_result.fetch_strategy,
        )
        session.add(jr)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("task_extract: completed job=%s", job_id)

    except Exception as exc:
        log.error("task_extract: failed job=%s error=%s", job_id, exc)
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()


async def _extract(content: str, schema: dict, prompt: str) -> dict:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL)

    user_content = (
        f"Extract data from this content matching this schema:\n\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"{'Instructions: ' + prompt + chr(10) if prompt else ''}"
        f"Content:\n{content[:8000]}"
    )

    resp = await client.chat.completions.create(
        model=_LLM_MODEL,
        messages=[
            {"role": "system", "content": "Extract structured data as JSON matching the given schema."},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content or "{}")
