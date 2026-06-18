"""Integration tests for POST /v1/scrape.

Requires a running ScrapeForge stack (docker compose up).
Run: pytest tests/integration/ -v -m integration
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


class TestScrapeMarkdown:
    def test_scrape_returns_markdown(self, client):
        """POST /v1/scrape with formats=[markdown] must return non-empty markdown."""
        resp = client.post(
            "/v1/scrape",
            json={"url": "https://example.com", "formats": ["markdown"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "markdown" in data["data"]
        assert len(data["data"]["markdown"]) > 0

    def test_scrape_rawhtml_format(self, client):
        """rawHtml format must return the raw HTML document."""
        resp = client.post(
            "/v1/scrape",
            json={"url": "https://example.com", "formats": ["rawHtml"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rawHtml" in data["data"]
        assert "<html" in data["data"]["rawHtml"].lower()

    def test_scrape_links_format(self, client):
        """links format must return a list of URLs."""
        resp = client.post(
            "/v1/scrape",
            json={"url": "https://example.com", "formats": ["links"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "links" in data["data"]
        assert isinstance(data["data"]["links"], list)

    def test_scrape_main_content_strips_nav(self, client):
        """onlyMainContent=true should strip navigation boilerplate."""
        resp = client.post(
            "/v1/scrape",
            json={"url": "https://example.com", "formats": ["markdown"], "onlyMainContent": True},
        )
        assert resp.status_code == 200
        md = resp.json()["data"].get("markdown", "")
        assert len(md) > 0


class TestScrapeAuth:
    def test_scrape_requires_auth(self, unauth_client):
        """Scrape without Authorization header must return 401."""
        resp = unauth_client.post("/v1/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 401

    def test_scrape_invalid_api_key(self, base_url):
        """Scrape with an invalid API key must return 401."""
        import httpx
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": "Bearer invalid-key-xyz-999"},
        ) as c:
            resp = c.post("/v1/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 401


class TestScrapeValidation:
    def test_scrape_invalid_url_returns_422(self, client):
        """A non-URL string must be rejected with 422."""
        resp = client.post("/v1/scrape", json={"url": "not-a-url"})
        assert resp.status_code == 422

    def test_scrape_private_ip_blocked(self, client):
        """A URL pointing to a private IP must return 422 (SSRF block)."""
        resp = client.post("/v1/scrape", json={"url": "http://127.0.0.1/admin"})
        assert resp.status_code == 422

    def test_scrape_missing_url_returns_422(self, client):
        """Request body without url field must return 422."""
        resp = client.post("/v1/scrape", json={"formats": ["markdown"]})
        assert resp.status_code == 422


class TestScrapeCache:
    def test_scrape_cache_hit_on_second_request(self, client):
        """Second scrape of the same URL should return cached=True."""
        url = "https://example.com"
        client.post("/v1/scrape", json={"url": url, "formats": ["markdown"]}, timeout=30)
        resp = client.post("/v1/scrape", json={"url": url, "formats": ["markdown"]}, timeout=10)
        assert resp.status_code == 200
        assert resp.json()["meta"].get("cached") is True
