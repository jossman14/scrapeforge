FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY worker/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app
COPY --from=builder /install /usr/local

# Copy both api and worker (worker imports from api for shared engine/models)
COPY api/ /app/api/
COPY worker/ /app/worker/

RUN chown -R appuser:appgroup /app
USER appuser

ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import redis; r=redis.from_url('redis://redis:6379/0'); r.ping()"

CMD ["python", "-m", "arq", "worker.main.WorkerSettings"]
