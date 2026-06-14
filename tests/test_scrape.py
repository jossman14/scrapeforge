"""Integration tests for POST /v1/scrape.

Requires a running scrapeforge stack or mocked dependencies.
Run: pytest tests/ -v
"""

from __future__ import annotations

import pytest
import httpx


BASE_URL = "http://localhost:3002"
API_KEY = "test-key-changeme"


@pytest.fixture
def client():
    return httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {API_KEY}"})


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_scrape_missing_auth():
    with httpx.Client(base_url=BASE_URL) as c:
        resp = c.post("/v1/scrape", json={"url": "https://example.com"})
    assert resp.status_code == 401


def test_scrape_invalid_url(client):
    resp = client.post("/v1/scrape", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_scrape_valid_url(client):
    resp = client.post(
        "/v1/scrape",
        json={"url": "https://example.com", "formats": ["markdown"]},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "markdown" in data["data"]
    assert len(data["data"]["markdown"]) > 0


def test_scrape_rawhtml_format(client):
    resp = client.post(
        "/v1/scrape",
        json={"url": "https://example.com", "formats": ["rawHtml"]},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "rawHtml" in data["data"]
    assert "<html" in data["data"]["rawHtml"].lower()


def test_scrape_links_format(client):
    resp = client.post(
        "/v1/scrape",
        json={"url": "https://example.com", "formats": ["links"]},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "links" in data["data"]
    assert isinstance(data["data"]["links"], list)


def test_map_endpoint(client):
    resp = client.post(
        "/v1/map",
        json={"url": "https://example.com"},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["urls"], list)


def test_crawl_endpoint_returns_job_id(client):
    resp = client.post(
        "/v1/crawl",
        json={"url": "https://example.com", "maxDepth": 1, "limit": 5},
        timeout=15,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "jobId" in data
    assert len(data["jobId"]) == 36  # UUID format


def test_batch_endpoint_returns_job_id(client):
    resp = client.post(
        "/v1/batch/scrape",
        json={"urls": ["https://example.com", "https://httpbin.org/html"]},
        timeout=15,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "jobId" in data


def test_extract_without_llm_key():
    """Extract should return 503 when LLM_API_KEY is not set."""
    with httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {API_KEY}"}) as c:
        resp = c.post(
            "/v1/extract",
            json={
                "url": "https://example.com",
                "schema": {"type": "object", "properties": {"title": {"type": "string"}}},
            },
            timeout=30,
        )
    # 503 = LLM not configured, 200 = LLM worked
    assert resp.status_code in (200, 503)


def test_jobs_not_found(client):
    resp = client.get("/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_scrape_cache_hit(client):
    """Second scrape of same URL should return cached=True."""
    url = "https://example.com"
    # First request
    client.post("/v1/scrape", json={"url": url, "formats": ["markdown"]}, timeout=30)
    # Second request
    resp = client.post("/v1/scrape", json={"url": url, "formats": ["markdown"]}, timeout=10)
    assert resp.status_code == 200
    assert resp.json()["meta"].get("cached") is True
