# ScrapeForge API Reference

Base URL: `http://localhost:3002`

All endpoints under `/v1/*` require an API key via `Authorization: Bearer <key>` header unless noted.

---

## Authentication

Every `/v1/*` request must include:

```
Authorization: Bearer sf_<your_api_key>
```

Missing or invalid keys return **401 Unauthorized**. Requests exceeding the per-key rate limit (default: 60 req/min) return **429 Too Many Requests** with a `Retry-After` header.

Create your first key via the admin dashboard at `http://localhost:3002/admin`.

---

## Endpoints

### POST /v1/scrape

Scrape a single URL synchronously. Returns the result directly (no job queue).

**Auth required:** Yes

**Request body:**

```json
{
  "url": "https://example.com",
  "formats": ["markdown", "html", "rawHtml", "links", "screenshot"],
  "onlyMainContent": true,
  "waitFor": 0,
  "timeout": 30,
  "respectRobotsTxt": true,
  "cacheTtl": 3600
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | — | URL to scrape (must be public HTTP/HTTPS) |
| `formats` | string[] | No | `["markdown"]` | Output formats: `markdown`, `html`, `rawHtml`, `links`, `screenshot` |
| `onlyMainContent` | boolean | No | `true` | Strip nav/ads/boilerplate; extract main content only |
| `waitFor` | integer | No | `0` | Wait N milliseconds for JS to render (0–30000) |
| `timeout` | integer | No | `30` | Fetch timeout in seconds (5–120) |
| `respectRobotsTxt` | boolean | No | `true` | Honour robots.txt rules for the target domain |
| `cacheTtl` | integer | No | `3600` | Cache result for N seconds in Redis (0 = no cache) |

**Response 200:**

```json
{
  "success": true,
  "data": {
    "markdown": "# Example Domain\n\nThis domain is for use...",
    "html": "<h1>Example Domain</h1>...",
    "rawHtml": "<!DOCTYPE html>...",
    "links": ["https://www.iana.org/domains/example"],
    "screenshot": null
  },
  "meta": {
    "url": "https://example.com",
    "statusCode": 200,
    "fetchStrategy": "static",
    "cached": false,
    "cachedAt": null
  }
}
```

**Error codes:**

| Code | Meaning |
|------|---------|
| 400 | Invalid URL (private IP, invalid scheme, robots.txt blocked) |
| 401 | Missing or invalid API key |
| 422 | Validation error (invalid field values) |
| 429 | Rate limit exceeded |
| 503 | All fetch strategies failed |

**Example:**

```bash
curl -X POST http://localhost:3002/v1/scrape \
  -H "Authorization: Bearer sf_your_key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "formats": ["markdown"]}'
```

---

### POST /v1/crawl

Recursively crawl a site from a seed URL. Returns a `jobId` immediately; use `GET /v1/jobs/{jobId}` to poll for status and results.

**Auth required:** Yes

**Request body:**

```json
{
  "url": "https://example.com",
  "maxDepth": 2,
  "limit": 100,
  "includePaths": ["/blog/*"],
  "excludePaths": ["/admin/*", "/login"],
  "allowExternal": false,
  "respectRobotsTxt": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | — | Seed URL for the crawl |
| `maxDepth` | integer | No | `2` | Max link depth from seed (1–10) |
| `limit` | integer | No | `100` | Max pages to crawl (1–1000) |
| `includePaths` | string[] | No | `[]` | Only crawl URLs matching these glob patterns |
| `excludePaths` | string[] | No | `[]` | Skip URLs matching these glob patterns |
| `allowExternal` | boolean | No | `false` | Follow links to other domains |
| `respectRobotsTxt` | boolean | No | `true` | Honour robots.txt |

**Response 202:**

```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

**Example:**

```bash
curl -X POST http://localhost:3002/v1/crawl \
  -H "Authorization: Bearer sf_your_key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.example.com", "maxDepth": 3, "limit": 50}'
```

---

### POST /v1/map

Discover all URLs on a site quickly using sitemap.xml and link extraction — without scraping page content.

**Auth required:** Yes

**Request body:**

```json
{
  "url": "https://example.com",
  "limit": 5000,
  "includeSitemap": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | — | Root URL to map |
| `limit` | integer | No | `5000` | Max URLs to return (1–5000) |
| `includeSitemap` | boolean | No | `true` | Parse sitemap.xml if available |

**Response 200:**

```json
{
  "success": true,
  "urls": [
    "https://example.com/",
    "https://example.com/about",
    "https://example.com/blog/post-1"
  ],
  "total": 3
}
```

**Example:**

```bash
curl -X POST http://localhost:3002/v1/map \
  -H "Authorization: Bearer sf_your_key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "limit": 100}'
```

---

### POST /v1/extract

Extract structured data from a URL using an LLM (OpenAI-compatible). Returns a `jobId` for async processing.

**Auth required:** Yes
**LLM required:** `LLM_API_KEY` must be configured; returns 503 otherwise.

**Request body:**

```json
{
  "url": "https://example.com/product/widget",
  "schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string"},
      "price": {"type": "number"},
      "in_stock": {"type": "boolean"}
    },
    "required": ["name", "price"]
  },
  "prompt": "Extract product information from this page.",
  "onlyMainContent": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | — | URL to extract from |
| `schema` | object | Yes | — | JSON Schema defining the expected output shape |
| `prompt` | string | No | `""` | Optional instructions to guide the LLM |
| `onlyMainContent` | boolean | No | `true` | Use main content only (strips boilerplate) |

**Response 202:**

```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440001",
  "status": "queued"
}
```

Poll `GET /v1/jobs/{jobId}` for the result. On completion, `results[0].extracted_data` contains the structured JSON.

**Example:**

```bash
curl -X POST http://localhost:3002/v1/extract \
  -H "Authorization: Bearer sf_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news.example.com/article/123",
    "schema": {
      "type": "object",
      "properties": {
        "title": {"type": "string"},
        "author": {"type": "string"},
        "published_at": {"type": "string"}
      }
    },
    "prompt": "Extract the article metadata."
  }'
```

---

### POST /v1/batch/scrape

Scrape multiple URLs concurrently as a single async job. Returns a `jobId`; poll `GET /v1/jobs/{jobId}` for results.

**Auth required:** Yes

**Request body:**

```json
{
  "urls": [
    "https://example.com/page-1",
    "https://example.com/page-2"
  ],
  "formats": ["markdown"],
  "onlyMainContent": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `urls` | string[] | Yes | — | URLs to scrape (1–100 items) |
| `formats` | string[] | No | `["markdown"]` | Output formats for each URL |
| `onlyMainContent` | boolean | No | `true` | Strip boilerplate |

**Response 202:**

```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440002",
  "status": "queued",
  "total": 2
}
```

**Example:**

```bash
curl -X POST http://localhost:3002/v1/batch/scrape \
  -H "Authorization: Bearer sf_your_key" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://example.com/a", "https://example.com/b"],
    "formats": ["markdown"]
  }'
```

---

### GET /v1/jobs/{jobId}

Get the status and results of an async job (crawl, extract, batch, or map).

**Auth required:** Yes

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `jobId` | UUID | Job ID returned by `/v1/crawl`, `/v1/extract`, `/v1/batch/scrape`, or `/v1/map` |

**Response 200:**

```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440000",
  "type": "crawl",
  "status": "completed",
  "createdAt": "2026-06-18T10:00:00Z",
  "startedAt": "2026-06-18T10:00:01Z",
  "completedAt": "2026-06-18T10:00:45Z",
  "error": null,
  "results": [
    {
      "url": "https://example.com/",
      "statusCode": 200,
      "markdown": "# Home\n\n...",
      "html": "<h1>Home</h1>...",
      "rawHtml": null,
      "links": null,
      "extractedData": null,
      "fetchStrategy": "static",
      "fetchedAt": "2026-06-18T10:00:05Z"
    }
  ],
  "totalResults": 1
}
```

**Job status values:**

| Status | Meaning |
|--------|---------|
| `queued` | Waiting for a worker |
| `running` | Worker is processing |
| `completed` | Finished successfully |
| `failed` | Finished with an error (see `error` field) |
| `cancelled` | Cancelled before completion |

**Error codes:**

| Code | Meaning |
|------|---------|
| 401 | Missing or invalid API key |
| 404 | Job not found |

**Example:**

```bash
curl http://localhost:3002/v1/jobs/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer sf_your_key"
```

---

### GET /health

Basic liveness probe. Returns 200 if the API process is running.

**Auth required:** No

**Response 200:**

```json
{"status": "ok"}
```

**Example:**

```bash
curl http://localhost:3002/health
```

---

### GET /ready

Readiness probe. Verifies PostgreSQL and Redis connectivity before reporting ready.

**Auth required:** No

**Response 200:**

```json
{"status": "ready", "db": "ok", "redis": "ok"}
```

**Response 503** (dependency unavailable):

```json
{"status": "not_ready", "db": "error", "redis": "ok", "detail": "DB ping failed"}
```

**Example:**

```bash
curl http://localhost:3002/ready
```

---

### GET /metrics

Prometheus metrics endpoint.

**Auth required:** No

**Response 200:** Plain-text Prometheus exposition format.

```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="POST",path="/v1/scrape",status="200"} 42
```

**Example:**

```bash
curl http://localhost:3002/metrics
```

---

## Admin Dashboard

Navigate to `http://localhost:3002/admin` in a browser. Log in with the `ADMIN_KEY` from your `.env` file.

The dashboard provides:
- Queue depth and worker health
- Job history (last 20 jobs with duration)
- API key management (create / revoke keys)
- 24-hour request metrics

---

## Error Response Format

All errors return structured JSON:

```json
{"detail": "Human-readable error message"}
```

Validation errors (422) include field-level detail:

```json
{
  "detail": [
    {
      "loc": ["body", "url"],
      "msg": "URL must use http or https scheme",
      "type": "value_error"
    }
  ]
}
```

---

## Rate Limits

Rate limiting is enforced per API key using an atomic Redis sliding-window:

- **Default:** 60 requests per 60-second window
- **Header on 429:** `Retry-After: <seconds>`
- Configurable per key via the admin dashboard

---

## SSRF Protection

The engine blocks requests to private/reserved IP ranges:

- Loopback: `127.0.0.0/8`
- Private: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Link-local / cloud metadata: `169.254.0.0/16`, `169.254.169.254`, `metadata.google.internal`

Blocked addresses return **400 Bad Request**.
