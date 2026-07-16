"""Pydantic v2 schemas for the Course Builder draft-authoring API (Task 2).

The HTTP surface for ``DraftCourse`` + ``DraftObjective`` CRUD. ``learner_profile``
and ``effort_config`` are stored verbatim as JSONB (``model_dump()`` dicts); the
``effort_config`` shape is the one Task 5's effort estimator will consume, so its
defaults live here as the single source of truth. ``key_skills`` is stored as a
``{"skills": [...]}`` JSONB envelope on the model and round-trips back to a flat
``list[str]`` on the wire (see ``router_course.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssetKind

BloomLevel = Literal[
    "remember", "understand", "apply", "analyze", "evaluate", "create"
]


class LearnerProfile(BaseModel):
    """Who the course is for — drives effort estimation + AI advising."""

    experience_level: str | None = None
    role: str | None = None
    goals: str | None = None
    weekly_hours_target: float | None = None
    in_class_hours_per_week: float | None = None
    motivation: str | None = None


class EffortConfig(BaseModel):
    """Tunable effort-estimation rates (Task 5 consumes this exact shape)."""

    present_min_per_slide: float = 2.0
    review_min_per_slide: float = 1.0
    study_words_per_minute: float = 150.0
    min_per_100_loc: float = 45.0
    min_per_problem: float = 8.0


class CourseCreate(BaseModel):
    title: str
    description: str | None = None
    learner_profile: LearnerProfile | None = None
    effort_config: EffortConfig | None = None
    target_weeks: int | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    learner_profile: LearnerProfile | None = None
    effort_config: EffortConfig | None = None
    target_weeks: int | None = None
    status: str | None = None


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    learner_profile: dict | None
    effort_config: dict | None
    target_weeks: int | None
    status: str
    curriculum_id: uuid.UUID | None
    created_at: datetime


class ObjectiveCreate(BaseModel):
    text: str
    bloom_level: BloomLevel = "understand"
    key_skills: list[str] = Field(default_factory=list)
    week_index: int | None = None
    order_index: int = 0


class ObjectiveUpdate(BaseModel):
    text: str | None = None
    bloom_level: BloomLevel | None = None
    key_skills: list[str] | None = None
    week_index: int | None = None
    order_index: int | None = None


class ObjectiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_course_id: uuid.UUID
    text: str
    bloom_level: str
    key_skills: list[str]
    week_index: int | None
    order_index: int


# ---------------------------------------------------------------------------
# Items (Task 3)
# ---------------------------------------------------------------------------
#
# ``kind`` rides the wire as the AssetKind *value* string (e.g. "slides"). On
# create it is optional: when omitted the router fills it via the rule-based
# ``categorize.guess_kind``. ``metrics`` is likewise optional and, when omitted,
# is inferred via ``categorize.extract_metrics`` — author-provided values win.


class ItemCreate(BaseModel):
    title: str
    kind: AssetKind | None = None
    content: str | None = None
    source_url: str | None = None
    metrics: dict | None = None
    week_index: int | None = None
    order_index: int = 0


class ItemUpdate(BaseModel):
    title: str | None = None
    kind: AssetKind | None = None
    content: str | None = None
    source_url: str | None = None
    metrics: dict | None = None
    week_index: int | None = None
    order_index: int | None = None
    estimated_minutes: int | None = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_course_id: uuid.UUID
    kind: AssetKind
    title: str
    content: str | None
    source_url: str | None
    metrics: dict | None
    week_index: int | None
    order_index: int
    estimated_minutes: int | None


class AlignmentCreate(BaseModel):
    objective_id: uuid.UUID


# ---------------------------------------------------------------------------
# Item media attachments (slice 2 — media in content)
# ---------------------------------------------------------------------------


class AttachMediaRequest(BaseModel):
    media_asset_id: uuid.UUID
    order_index: int = 0


class ItemMediaOut(BaseModel):
    """A media asset attached to a draft item (link + asset display fields)."""

    model_config = ConfigDict(from_attributes=True)

    media_asset_id: uuid.UUID
    order_index: int
    kind: str
    filename: str
    mime: str
    status: str
    duration_s: float | None = None


# ---------------------------------------------------------------------------
# Dependencies (Task 4)
# ---------------------------------------------------------------------------


class DependencyCreate(BaseModel):
    from_item_id: uuid.UUID
    to_item_id: uuid.UUID
    edge_type: Literal["prerequisite", "supports"] = "prerequisite"


class DependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_course_id: uuid.UUID
    from_item_id: uuid.UUID
    to_item_id: uuid.UUID
    edge_type: str
    source: str
    accepted: bool


# ---------------------------------------------------------------------------
# Advisor notes (Phase 2 AI co-pilot — Task 1)
# ---------------------------------------------------------------------------


class AdvisorNoteOut(BaseModel):
    """Wire representation of a persisted DraftAdvisorNote."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_course_id: uuid.UUID
    target_kind: str | None
    target_ref: str | None
    kind: str
    text: str
    status: str
    created_at: datetime


class AdviseRequest(BaseModel):
    """Body for POST /courses/{course_id}/advise — optional focus hint."""

    focus: str | None = None


class AdvisorNoteStatusUpdate(BaseModel):
    """Body for PATCH /advisor-notes/{note_id} — flip status."""

    status: Literal["accepted", "dismissed"]


class InferDepsResult(BaseModel):
    """Response for POST /courses/{course_id}/infer-deps.

    ``suggested_created`` — number of new AI-suggested prerequisite edges
    persisted (source="ai_suggested", accepted=False).
    ``missing_flagged`` — number of DraftAdvisorNote warning rows created for
    items whose dependencies are not covered by earlier course content.
    """

    suggested_created: int
    missing_flagged: int
