"""Integration tests for POST /v1/crawl.

Requires a running ScrapeForge stack.
"""

from __future__ import annotations

import time
import pytest


pytestmark = pytest.mark.integration


class TestCrawlEndpoint:
    def test_crawl_creates_job(self, client):
        """POST /v1/crawl must return a jobId with a valid UUID format."""
        resp = client.post(
            "/v1/crawl",
            json={"url": "https://example.com", "maxDepth": 1, "limit": 5},
            timeout=15,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "jobId" in data
        assert len(data["jobId"]) == 36  # UUID format: 8-4-4-4-12

    def test_crawl_job_visible_in_get_jobs(self, client):
        """The job returned by /v1/crawl must be retrievable via GET /v1/jobs/{id}."""
        resp = client.post(
            "/v1/crawl",
            json={"url": "https://example.com", "maxDepth": 1, "limit": 2},
            timeout=15,
        )
        assert resp.status_code == 202
        job_id = resp.json()["jobId"]

        job_resp = client.get(f"/v1/jobs/{job_id}")
        assert job_resp.status_code == 200
        job_data = job_resp.json()
        assert job_data["id"] == job_id

    def test_crawl_invalid_url_rejected(self, client):
        """Non-URL value for url must return 422."""
        resp = client.post("/v1/crawl", json={"url": "not-a-url"})
        assert resp.status_code == 422

    def test_crawl_requires_auth(self, unauth_client):
        """Crawl without auth header must return 401."""
        resp = unauth_client.post(
            "/v1/crawl",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 401

    def test_crawl_max_depth_boundary(self, client):
        """maxDepth at minimum (1) must be accepted; 0 must be rejected."""
        resp_valid = client.post(
            "/v1/crawl",
            json={"url": "https://example.com", "maxDepth": 1},
            timeout=10,
        )
        assert resp_valid.status_code == 202

        resp_invalid = client.post(
            "/v1/crawl",
            json={"url": "https://example.com", "maxDepth": 0},
        )
        assert resp_invalid.status_code == 422
