"""Workflow models: ChangeRequest, QAReview, Approval."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped
from app.models.enums import LifecycleStatus


class ChangeRequest(TenantScoped, Base):
    __tablename__ = "change_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_bump: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Jira-style external tracker link (e.g. Jira issue URL, GitHub issue URL). B6.
    external_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reserved: populated by the dependency-cascade engine in Milestone B (impact analysis).
    impact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Structured executable change-set (the ReleaseRequest shape minus
    # expected_active_id/ccr_id/note): {bump, changed[], added[], removed[],
    # edges_added[], edges_removed[]}. Replayed through fork() at merge time
    # (PR-style review → merge). Null for description-only CCRs.
    change_set: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[LifecycleStatus] = mapped_column(
        nullable=False, default=LifecycleStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QAReview(TenantScoped, Base):
    __tablename__ = "qa_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ccr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    dimension_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-dimension evidence {dim: "<evidence string>"} for AI-drafted reviews
    # (C3); None for human reviews. dimension_scores stays a flat {dim: int} for
    # both so C4's eval can compare scores uniformly.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Approval(TenantScoped, Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("ccr_id", "approver_id", name="uq_approval_ccr_approver"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ccr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
