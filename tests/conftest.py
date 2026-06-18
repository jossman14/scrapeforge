"""Shared pytest fixtures for unit and integration tests."""

from __future__ import annotations

import os
import pytest
import httpx


# ---------------------------------------------------------------------------
# Integration-test configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("SCRAPEFORGE_BASE_URL", "http://localhost:3002")
TEST_API_KEY = os.environ.get("SCRAPEFORGE_TEST_KEY", "test-key-changeme")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def api_key() -> str:
    return TEST_API_KEY


@pytest.fixture
def client(base_url: str, api_key: str):
    """Synchronous httpx client with auth header, for integration tests."""
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    ) as c:
        yield c


@pytest.fixture
def unauth_client(base_url: str):
    """Synchronous httpx client WITHOUT auth header."""
    with httpx.Client(base_url=base_url, timeout=15) as c:
        yield c
