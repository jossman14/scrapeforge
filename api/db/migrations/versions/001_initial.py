"""Initial schema: api_keys, jobs, job_results, crawl_frontier, url_cache, audit_log.

Revision ID: 001
Revises:
Create Date: 2026-06-13

Notes
-----
- gen_random_uuid() is PostgreSQL 13+ built-in; no pgcrypto extension needed.
- pg_stat_statements is created here; requires `shared_preload_libraries =
  'pg_stat_statements'` in postgresql.conf to be active (already set in the
  Docker image via POSTGRES_INITDB_ARGS or a custom config file).
- All timestamps are TIMESTAMP WITH TIME ZONE (TIMESTAMPTZ).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pg_stat_statements for query observability.
    # Requires shared_preload_libraries in postgresql.conf — safe to create
    # even if not yet active; will take effect after PostgreSQL restart.
    # NOTE: CockroachDB does not support CREATE EXTENSION at all (unimplemented
    # feature). Skip on non-PostgreSQL dialects (deployment change, see pm2
    # migration notes) instead of failing the whole migration.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "rate_limit_per_minute",
            sa.Integer(),
            server_default=sa.text("60"),
            nullable=False,
        ),
    )
    op.create_index("idx_api_keys_key_hash", "api_keys", ["key_hash"])

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.CheckConstraint(
            "type IN ('scrape', 'crawl', 'map', 'extract', 'batch')",
            name="chk_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="chk_jobs_status",
        ),
    )
    op.create_index("idx_jobs_status_created_at", "jobs", ["status", "created_at"])
    op.create_index(
        "idx_jobs_api_key_id_created_at", "jobs", ["api_key_id", "created_at"]
    )

    # ------------------------------------------------------------------
    # job_results
    # ------------------------------------------------------------------
    op.create_table(
        "job_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column(
            "links", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column(
            "extracted_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("fetch_strategy", sa.String(20), nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "fetch_strategy IN ('static', 'stealthy', 'playwright')",
            name="chk_job_results_fetch_strategy",
        ),
    )
    op.create_index("idx_job_results_job_id", "job_results", ["job_id"])
    op.create_index(
        "idx_job_results_url_fetched_at", "job_results", ["url", "fetched_at"]
    )

    # ------------------------------------------------------------------
    # crawl_frontier
    # ------------------------------------------------------------------
    op.create_table(
        "crawl_frontier",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "discovered_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'skipped')",
            name="chk_crawl_frontier_status",
        ),
        sa.UniqueConstraint("job_id", "url", name="uq_crawl_frontier_job_id_url"),
    )
    op.create_index(
        "idx_crawl_frontier_job_id_status_depth",
        "crawl_frontier",
        ["job_id", "status", "depth"],
    )

    # ------------------------------------------------------------------
    # url_cache
    # TTL cleanup: DELETE FROM url_cache WHERE expires_at < now();
    # Schedule hourly via pg_cron or arq beat.
    # ------------------------------------------------------------------
    op.create_table(
        "url_cache",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("url_hash", sa.CHAR(64), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=True),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "cached_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("url_hash", name="uq_url_cache_url_hash"),
    )
    op.create_index("idx_url_cache_expires_at", "url_cache", ["expires_at"])

    # ------------------------------------------------------------------
    # audit_log  (append-only)
    # Retention cleanup: DELETE FROM audit_log WHERE created_at < now() - INTERVAL '90 days';
    # Schedule weekly.
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_audit_log_api_key_id_created_at",
        "audit_log",
        ["api_key_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("url_cache")
    op.drop_table("crawl_frontier")
    op.drop_table("job_results")
    op.drop_table("jobs")
    op.drop_table("api_keys")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
