"""
SQLAlchemy 2.x async ORM models for ScrapeForge.

All PKs use gen_random_uuid() (PostgreSQL 13+ built-in, no extension needed).
All timestamps are TIMESTAMP WITH TIME ZONE (TIMESTAMPTZ).

N+1 query guidance
------------------
Relationships are lazy-loaded by default. Callers MUST use explicit eager loading
to avoid N+1:

  from sqlalchemy.orm import selectinload, joinedload

  # Load jobs with their results in one round-trip:
  stmt = select(Job).options(selectinload(Job.results)).where(...)

  # Load api_key alongside a batch of jobs:
  stmt = select(Job).options(joinedload(Job.api_key)).where(...)

Never iterate over a list of ORM objects and access a relationship attribute
without pre-loading — each access hits the database.

Cleanup strategy (url_cache & audit_log)
-----------------------------------------
url_cache:
  Run a periodic DELETE every hour (e.g. pg_cron or an arq beat task):
    DELETE FROM url_cache WHERE expires_at < now();
  idx_url_cache_expires_at makes this O(expired_rows) not O(total_rows).

audit_log:
  Retain for at least 90 days by policy. Run a periodic DELETE weekly:
    DELETE FROM audit_log WHERE created_at < now() - INTERVAL '90 days';
  idx_audit_log_api_key_id_created_at covers the created_at range scan.
  For high-volume deployments partition audit_log by created_at (monthly) so
  old partitions can be DROPped instead of row-by-row deletion.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import text


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    """Hashed API credentials. NEVER store the plaintext key — only argon2/bcrypt hash."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    last_used_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, server_default=text("60"), nullable=False
    )

    # Use selectinload(ApiKey.jobs) to avoid N+1 when iterating api keys
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="api_key")
    audit_logs: Mapped[list[AuditLog]] = relationship(
        "AuditLog", back_populates="api_key"
    )

    __table_args__ = (Index("idx_api_keys_key_hash", "key_hash"),)


class Job(Base):
    """Async job record for scrape/crawl/map/extract/batch operations."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'queued'")
    )
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    options: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    started_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Use selectinload(Job.results) when fetching a list of jobs with results
    api_key: Mapped[Optional[ApiKey]] = relationship("ApiKey", back_populates="jobs")
    results: Mapped[list[JobResult]] = relationship(
        "JobResult", back_populates="job", cascade="all, delete-orphan"
    )
    crawl_frontier: Mapped[list[CrawlFrontier]] = relationship(
        "CrawlFrontier", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('scrape', 'crawl', 'map', 'extract', 'batch')",
            name="chk_jobs_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="chk_jobs_status",
        ),
        Index("idx_jobs_status_created_at", "status", "created_at"),
        Index("idx_jobs_api_key_id_created_at", "api_key_id", "created_at"),
    )


class JobResult(Base):
    """Per-URL result produced by a Job worker."""

    __tablename__ = "job_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    links: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Tracks which fallback tier was used: static < stealthy < playwright
    fetch_strategy: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fetched_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    cache_ttl_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    job: Mapped[Job] = relationship("Job", back_populates="results")

    __table_args__ = (
        CheckConstraint(
            "fetch_strategy IN ('static', 'stealthy', 'playwright')",
            name="chk_job_results_fetch_strategy",
        ),
        Index("idx_job_results_job_id", "job_id"),
        Index("idx_job_results_url_fetched_at", "url", "fetched_at"),
    )


class CrawlFrontier(Base):
    """URL queue for recursive crawl jobs. One row per discovered URL."""

    __tablename__ = "crawl_frontier"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    discovered_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    processed_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    job: Mapped[Job] = relationship("Job", back_populates="crawl_frontier")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'skipped')",
            name="chk_crawl_frontier_status",
        ),
        UniqueConstraint("job_id", "url", name="uq_crawl_frontier_job_id_url"),
        Index("idx_crawl_frontier_job_id_status_depth", "job_id", "status", "depth"),
    )


class UrlCache(Base):
    """
    Content-addressed cache keyed by SHA-256 of normalized URL.

    Cleanup: DELETE FROM url_cache WHERE expires_at < now();
    Run every hour via pg_cron or an arq periodic task.
    idx_url_cache_expires_at keeps this fast regardless of table size.
    """

    __tablename__ = "url_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    url_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    cached_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_url_cache_url_hash"),
        Index("idx_url_cache_expires_at", "expires_at"),
    )


class AuditLog(Base):
    """
    Append-only audit trail. Rows must never be updated — only inserted or
    deleted by the retention cleanup job.

    Cleanup: DELETE FROM audit_log WHERE created_at < now() - INTERVAL '90 days';
    Run weekly. For high-volume deployments, consider range partitioning by
    created_at (monthly) so old partitions can be DROPped instead.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    created_at: Mapped[Optional[object]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    api_key: Mapped[Optional[ApiKey]] = relationship(
        "ApiKey", back_populates="audit_logs"
    )

    __table_args__ = (
        Index("idx_audit_log_api_key_id_created_at", "api_key_id", "created_at"),
    )
