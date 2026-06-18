"""Integration tests for POST /v1/map.

Requires a running ScrapeForge stack.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


class TestMapEndpoint:
    def test_map_returns_urls(self, client):
        """POST /v1/map must return a non-empty list of URLs for a real site."""
        resp = client.post(
            "/v1/map",
            json={"url": "https://example.com"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "urls" in data
        assert isinstance(data["urls"], list)

    def test_map_requires_auth(self, unauth_client):
        """Map without auth must return 401."""
        resp = unauth_client.post("/v1/map", json={"url": "https://example.com"})
        assert resp.status_code == 401

    def test_map_invalid_url(self, client):
        """Non-URL string must return 422."""
        resp = client.post("/v1/map", json={"url": "not-a-url"})
        assert resp.status_code == 422

    def test_map_private_ip_blocked(self, client):
        """SSRF: URL resolving to private IP must return 422."""
        resp = client.post("/v1/map", json={"url": "http://192.168.1.1/"})
        assert resp.status_code == 422
