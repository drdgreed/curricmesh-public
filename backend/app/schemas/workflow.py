"""Pydantic v2 schemas for CCR, QAReview, Approval, and Dashboard."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.core.versioning.semver import BumpType
from app.models.enums import AssetKind, LifecycleStatus
from app.schemas.release import ReleaseChangeSet


# ---------------------------------------------------------------------------
# CCR
# ---------------------------------------------------------------------------


class CCRCreate(BaseModel):
    curriculum_id: uuid.UUID
    title: str
    rationale: str | None = None
    proposed_bump: BumpType  # "major" | "minor" | "patch" — enum-typed, no manual validation needed
    affected_kinds: list[AssetKind]
    instructor_override: bool = False
    target_version_id: uuid.UUID | None = None
    affected_asset_ids: list[uuid.UUID] | None = None
    # Jira-style external tracker link (e.g. Jira issue URL). Optional. B6.
    external_link: str | None = None
    # Structured executable change-set for PR-style review → merge. When present,
    # POST /ccrs/{id}/merge replays it through fork() once the CCR is approved.
    # Null = description-only CCR (no executable payload; merge → 400).
    change_set: ReleaseChangeSet | None = None


class CCROut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    curriculum_id: uuid.UUID
    author_id: uuid.UUID | None
    title: str
    rationale: str | None
    proposed_bump: str | None
    external_link: str | None = None
    # AI evidence (C2) lives under impact["ai_research"]; the AI-findings inbox
    # surfaces impact.ai_research.citations/topic/coverage_status to reviewers.
    impact: dict | None = None
    # The structured executable change-set (PR-style review → merge), or null for
    # description-only CCRs. Persisted JSONB; serialized back as the change-set shape.
    change_set: ReleaseChangeSet | None = None
    status: LifecycleStatus
    created_at: datetime


# ---------------------------------------------------------------------------
# QA Review
# ---------------------------------------------------------------------------


class QAReviewCreate(BaseModel):
    dimension_scores: dict[str, int]
    verdict: Literal["pass", "fail"]


class QAReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ccr_id: uuid.UUID
    reviewer_id: uuid.UUID | None
    dimension_scores: dict | None
    # Per-dimension evidence for AI-drafted reviews (C3); None for human reviews.
    evidence: dict | None = None
    verdict: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# AI-findings inbox (C5)
# ---------------------------------------------------------------------------


class AIDraftQAOut(BaseModel):
    """An inert AI-draft QA review (verdict='ai_draft') surfaced for human review."""

    id: uuid.UUID
    ccr_id: uuid.UUID
    ccr_title: str | None
    dimension_scores: dict | None
    evidence: dict | None
    created_at: datetime


class AIInboxOut(BaseModel):
    drafted_ccrs: list[CCROut]
    draft_qa_reviews: list[AIDraftQAOut]


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApprovalCreate(BaseModel):
    decision: Literal["approve", "reject"]


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ccr_id: uuid.UUID
    approver_id: uuid.UUID | None
    role: str | None
    decision: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardVersionSummary(BaseModel):
    id: uuid.UUID
    semver: str
    status: LifecycleStatus
    created_at: datetime


class DashboardCohortSummary(BaseModel):
    id: uuid.UUID
    name: str
    version_id: uuid.UUID | None
    start_date: date | None = None
    end_date: date | None = None


class MisalignmentEntry(BaseModel):
    dependent_asset_id: uuid.UUID
    dependency_asset_id: uuid.UUID
    reason: str
    # Friendly names + latest-version timestamps resolved by the dashboard router.
    # The UUID-laden `reason` stays as an internal/fallback field; the frontend
    # renders from these structured fields.
    dependent_asset_name: str
    dependency_asset_name: str
    dependent_updated_at: datetime | None = None
    dependency_updated_at: datetime | None = None


class DashboardCurriculumEntry(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    current_version_id: uuid.UUID | None
    versions: list[DashboardVersionSummary]
    cohorts: list[DashboardCohortSummary]
    alignment: list[MisalignmentEntry] = []


class DashboardHistoryEntry(BaseModel):
    id: uuid.UUID
    event_type: str
    target: str | None
    actor_id: uuid.UUID | None
    details: dict[str, Any] | None
    created_at: datetime
    # Human-readable resolutions of actor_id and target, computed by the router.
    actor_label: str | None = None
    target_label: str | None = None


class DashboardOut(BaseModel):
    curricula: list[DashboardCurriculumEntry]
    recent_events: list[DashboardHistoryEntry]
