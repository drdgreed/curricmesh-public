"""Learner-delivery model (Phase 2, Foundation 1 — self-paced individual).

The consumption half of the platform: an ``Enrollment`` binds a learner to a
**released, immutable** ``CurriculumVersion`` (Phase 1's active version). Enrolling
**pins** that exact version, so a later re-release never shifts a learner
mid-course — the immutability of the content model carries through to delivery.
``LearnerProgress`` tracks one row per learner per item; ``AssessmentSubmission``
stores a learner's answer to an assessment item (score/feedback populated later
by Phase B, or left null for self-assessment in v1).

All three tables are ``TenantScoped`` and join the fail-closed RLS regime (see
``app/db/rls.py`` and the ``..._learner_delivery_tables`` migration). ``status``
columns are plain ``String`` (not native PG enums) so the test harness's
drop/recreate cycle stays simple — mirrors ``app/models/version_pin.py``.

Identity note: ``learner_id`` is the JWT ``sub`` (a ``users.id``) but carries no
DB-level FK — mirrors ``MediaAsset.created_by`` — so the delivery layer never
couples to the cross-tenant ``users`` table (users is not RLS-scoped).
``content_member_id`` references the pinned version's ``version_members.id`` —
the stable, enumerable per-version item id that makes the version renderable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class Enrollment(TenantScoped, Base):
    """A learner's enrollment in a released ``CurriculumVersion`` (pins it)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        # A learner enrolls at most once per pinned version.
        UniqueConstraint(
            "learner_id",
            "curriculum_version_id",
            name="uq_enrollments_learner_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The learner (JWT sub / users.id). No FK — see module docstring.
    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # The released, immutable version this enrollment pins.
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | completed | withdrawn
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LearnerProgress(TenantScoped, Base):
    """One row per learner per item — drives the progress bar + completion."""

    __tablename__ = "learner_progress"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id",
            "content_member_id",
            name="uq_learner_progress_enrollment_member",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The item within the pinned version (version_members.id).
    content_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("version_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_started"
    )  # not_started | in_progress | complete
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AssessmentSubmission(TenantScoped, Base):
    """A learner's answer to an assessment item (score/feedback via Phase B)."""

    __tablename__ = "assessment_submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("version_members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Populated by Phase B's assessment-feedback tutor, or left null (self-assess).
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
