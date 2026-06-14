FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY api/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app
COPY --from=builder /install /usr/local

# Copy API source (parent context = scrapeforge/)
COPY api/ /app/api/

RUN chown -R appuser:appgroup /app
USER appuser

ENV PYTHONPATH=/app
EXPOSE 3002

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -f http://localhost:3002/health || exit 1

# Run alembic migrations then start the server
CMD ["sh", "-c", "cd /app/api && python -m alembic upgrade head && cd /app && python -m uvicorn api.main:app --host 0.0.0.0 --port 3002 --workers 2"]
