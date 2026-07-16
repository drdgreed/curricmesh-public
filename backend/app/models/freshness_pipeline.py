"""Freshness-pipeline models: SourceWatchItem, SyllabusSnapshot, PipelineSeen, PipelineRun.

All four are TenantScoped — they carry ``organization_id`` via the mixin and
participate in the same fail-closed RLS regime as the other curriculum-domain
tables.  Their migration (revises c7e1a9f4d2b6) adds the RLS policies by
mirroring the ``d4f6a8c0e1b3_sync_logs`` pattern.

Design notes:
- ``SyllabusSnapshot.topics`` is JSONB: ``{"topics": [...]}`` — structured for
  the university adapter's diff logic.
- ``PipelineSeen`` has a unique constraint on ``(signal_id, organization_id)``
  so the dedup guard is DB-enforced, not just application-layer.
- ``PipelineRun.stats`` is JSONB; keys are defined in runner.py and consumed by
  digest.py.  Nullable so a run row can be opened before stats are known.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class SourceWatchItem(TenantScoped, Base):
    """University / institution syllabus page to watch for curriculum changes."""

    __tablename__ = "source_watch_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    institution: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    search_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SyllabusSnapshot(TenantScoped, Base):
    """Captured snapshot of a watch item's extracted syllabus topics."""

    __tablename__ = "syllabus_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    watch_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_watch_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(
        String(16), nullable=False, default="fetched"
    )  # fetched | search_only
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PipelineSeen(TenantScoped, Base):
    """Dedup ledger: tracks which signal IDs have already been processed per org."""

    __tablename__ = "freshness_pipeline_seen"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    signal_id: Mapped[str] = mapped_column(Text, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "signal_id", "organization_id", name="uq_pipeline_seen_signal_org"
        ),
    )


class PipelineRun(TenantScoped, Base):
    """Audit record for a single freshness pipeline execution."""

    __tablename__ = "freshness_pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running"
    )  # running | ok | failed
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    digest_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class GapAssessment(TenantScoped, Base):
    """The Judge's memory: one row per (curriculum, topic) — simultaneously the
    monitor queue (recommendation='monitor'), the reject memory, the promotion
    record (promoted_ccr_id), and the audit trail of every judgment."""

    __tablename__ = "gap_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curriculum_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curricula.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)          # LOWERCASE identity
    display_topic: Mapped[str] = mapped_column(Text, nullable=False)  # raw casing for UI
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False)  # adopt_now | monitor | reject
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False)       # 7 dims + model_recommendation (+ prev_confidence on re-eval)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    dossier: Mapped[list] = mapped_column(JSONB, nullable=False)      # [{run_date, source_kinds, evidence}]
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    times_seen_at_last_eval: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    promoted_ccr_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("curriculum_id", "topic", "organization_id", name="uq_gap_assessment_curriculum_topic_org"),)
