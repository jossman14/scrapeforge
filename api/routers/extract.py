"""POST /v1/extract — structured data extraction via LLM.

Scrapes a URL then calls an OpenAI-compatible LLM to extract data
matching a user-provided Pydantic/JSON schema.
Returns HTTP 503 if LLM_API_KEY is not configured.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.rate_limit import check_rate_limit
from api.db.models import ApiKey
from api.db.session import get_session
from api.deps import get_redis
from api.engine.fetch import FetchOptions, fetch_with_fallback
from api.engine.convert import html_to_markdown
from api.engine.url_validator import validate_url

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["extract"])

_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")


class ExtractRequest(BaseModel):
    url: AnyHttpUrl
    schema_: dict = Field(..., alias="schema", description="JSON Schema for the data to extract")
    prompt: str = Field(default="", description="Optional extraction instructions")
    only_main_content: bool = Field(default=True, alias="onlyMainContent")

    model_config = {"populate_by_name": True}


class ExtractResponse(BaseModel):
    success: bool = True
    data: Any
    meta: dict = {}


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    body: ExtractRequest,
    api_key: ApiKey = Depends(check_rate_limit),
    redis: aioredis.Redis = Depends(get_redis),
) -> ExtractResponse:
    if not _LLM_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM extraction not available. Set LLM_API_KEY environment variable.",
        )

    url = str(body.url)
    try:
        await validate_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # Fetch the page
    result = await fetch_with_fallback(url, FetchOptions(timeout=30))
    if not result.html and result.error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch URL: {result.error}",
        )

    markdown = html_to_markdown(result.html, url, body.only_main_content)

    # Call LLM for structured extraction
    extracted = await _llm_extract(markdown, body.schema_, body.prompt)

    return ExtractResponse(
        success=True,
        data=extracted,
        meta={"url": url, "fetch_strategy": result.fetch_strategy},
    )


async def _llm_extract(content: str, schema: dict, prompt: str) -> Any:
    """Call OpenAI-compatible API to extract structured data."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL)

        system_prompt = (
            "You are a structured data extraction assistant. "
            "Extract data from the provided content according to the JSON schema. "
            "Return ONLY valid JSON matching the schema, no explanations."
        )

        user_prompt = (
            f"Extract data from this content and return JSON matching this schema:\n\n"
            f"Schema: {json.dumps(schema)}\n\n"
            f"{'Instructions: ' + prompt + chr(10) + chr(10) if prompt else ''}"
            f"Content:\n{content[:8000]}"
        )

        resp = await client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)

    except Exception as exc:
        log.error("llm_extract_error error=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM extraction failed. Check server logs for details.",
        )
