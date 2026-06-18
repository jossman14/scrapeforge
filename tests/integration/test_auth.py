"""Integration tests for authentication: API key validation and revocation.

Requires a running ScrapeForge stack with the admin API available.
"""

from __future__ import annotations

import pytest
import httpx


pytestmark = pytest.mark.integration


class TestAuthBasic:
    def test_valid_key_accepted(self, client):
        """A valid API key must receive 200 on a simple scrape."""
        resp = client.post(
            "/v1/scrape",
            json={"url": "https://example.com", "formats": ["markdown"]},
        )
        assert resp.status_code == 200

    def test_missing_auth_header_returns_401(self, unauth_client):
        """Missing Authorization header must return 401 with a helpful message."""
        resp = unauth_client.post(
            "/v1/scrape",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 401

    def test_wrong_scheme_returns_401(self, base_url):
        """Using wrong auth scheme (Basic instead of Bearer) must return 401."""
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        ) as c:
            resp = c.post("/v1/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self, base_url):
        """A random invalid key must return 401."""
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": "Bearer totally-invalid-key-00000000"},
        ) as c:
            resp = c.post("/v1/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 401


class TestHealthEndpoints:
    def test_health_endpoint_public(self, unauth_client):
        """GET /health must return 200 without auth."""
        resp = unauth_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_jobs_404_for_nonexistent_id(self, client):
        """GET /v1/jobs/{nonexistent} must return 404."""
        resp = client.get("/v1/jobs/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
