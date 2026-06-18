"""Integration tests for POST /v1/batch/scrape.

Requires a running ScrapeForge stack.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


class TestBatchEndpoint:
    def test_batch_returns_job_id(self, client):
        """POST /v1/batch/scrape with valid URLs must return a jobId."""
        resp = client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com", "https://httpbin.org/html"]},
            timeout=15,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "jobId" in data
        assert len(data["jobId"]) == 36

    def test_batch_single_url(self, client):
        """Batch with a single URL must still return a job."""
        resp = client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com"]},
            timeout=15,
        )
        assert resp.status_code == 202
        assert "jobId" in resp.json()

    def test_batch_job_retrievable(self, client):
        """The batch job must be visible via GET /v1/jobs/{id}."""
        resp = client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com"]},
            timeout=15,
        )
        assert resp.status_code == 202
        job_id = resp.json()["jobId"]

        job_resp = client.get(f"/v1/jobs/{job_id}")
        assert job_resp.status_code == 200

    def test_batch_requires_auth(self, unauth_client):
        """Batch without auth must return 401."""
        resp = unauth_client.post(
            "/v1/batch/scrape",
            json={"urls": ["https://example.com"]},
        )
        assert resp.status_code == 401

    def test_batch_empty_urls_rejected(self, client):
        """Empty urls list should return 422."""
        resp = client.post("/v1/batch/scrape", json={"urls": []})
        assert resp.status_code == 422

    def test_batch_invalid_url_rejected(self, client):
        """A non-URL in the urls list must return 422."""
        resp = client.post(
            "/v1/batch/scrape",
            json={"urls": ["not-a-url", "https://example.com"]},
        )
        assert resp.status_code == 422
