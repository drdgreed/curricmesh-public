"""Mutable *draft* authoring model for the Course Builder.

These seven tables hold a course **while it is being authored** in the Course
Builder (``docs/specs/2026-06-08-course-builder-design.md``). Unlike the
immutable, content-addressed version model (``app/models/content_model.py``),
draft rows are freely MUTATED in place; only once the author compiles the draft
is it materialized into the immutable model. They sit alongside everything else
and nothing in the immutable read path reads them.

* ``DraftCourse``       — the draft itself (title, learner profile, status…).
* ``DraftObjective``    — a learning objective being authored for the course.
* ``DraftItem``         — a draft content item (lesson, lab, assessment…).
* ``DraftItemObjective``— join: which objectives an item covers.
* ``DraftDependency``   — author/AI-proposed prerequisite edge between items.
* ``DraftRubricResult`` — a stored rubric evaluation (RAG/score) for the draft.
* ``DraftAdvisorNote``  — an AI/author note attached to the draft or a target.
* ``DraftItemMedia``    — join: an owned ``MediaAsset`` attached to a draft item
                          (slice 2 — media in content).

All are ``TenantScoped`` and join the RLS regime (see
``app/db/rls.py`` and the ``..._course_builder_draft_model`` migration). Style
mirrors ``app/models/content_model.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped
from app.models.enums import AssetKind


class DraftCourse(TenantScoped, Base):
    """A course being authored in the Course Builder (mutable draft)."""

    __tablename__ = "draft_courses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    learner_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    effort_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="drafting"
    )
    curriculum_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DraftObjective(TenantScoped, Base):
    """A learning objective being authored for a draft course."""

    __tablename__ = "draft_objectives"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bloom_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="understand"
    )
    key_skills: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    week_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DraftItem(TenantScoped, Base):
    """A draft content item (lesson, lab, assessment, …) in a draft course."""

    __tablename__ = "draft_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[AssetKind] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    week_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class DraftItemObjective(TenantScoped, Base):
    """Join: which draft objectives a draft item covers."""

    __tablename__ = "draft_item_objectives"
    __table_args__ = (
        UniqueConstraint(
            "draft_item_id",
            "draft_objective_id",
            name="uq_draft_item_objectives_item_obj",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    draft_objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_objectives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class DraftItemMedia(TenantScoped, Base):
    """Join: an owned ``MediaAsset`` attached to a draft item (usage link).

    Associates a tenant's ``MediaAsset`` with a ``DraftItem`` so the author can
    embed it in the item's content (``![[media:{media_asset_id}]]``) and so
    :func:`app.builder.compile.publish_draft` can freeze the referenced assets
    into the immutable content model on release. ``order_index`` preserves the
    author's ordering of attachments on an item. The (item, asset) pair is
    unique — an asset is attached to an item at most once.
    """

    __tablename__ = "draft_item_media"
    __table_args__ = (
        UniqueConstraint(
            "draft_item_id",
            "media_asset_id",
            name="uq_draft_item_media_item_asset",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DraftDependency(TenantScoped, Base):
    """An author/AI-proposed prerequisite edge between two draft items."""

    __tablename__ = "draft_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "draft_course_id",
            "from_item_id",
            "to_item_id",
            name="uq_draft_dependencies_course_from_to",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edge_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="prerequisite"
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="author")
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DraftRubricResult(TenantScoped, Base):
    """A stored rubric evaluation (RAG/score) for a draft course or scope."""

    __tablename__ = "draft_rubric_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rubric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rag: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DraftAdvisorNote(TenantScoped, Base):
    """An AI/author note attached to a draft course or a specific target."""

    __tablename__ = "draft_advisor_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    draft_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
