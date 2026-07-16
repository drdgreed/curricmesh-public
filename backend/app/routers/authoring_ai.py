"""Router: /api/v1/builder — author-time AI GENERATORS (Authoring Platform slice 3).

Extends the co-pilots from advisory to GENERATIVE. Three per-aspect endpoints,
each returning an editable DRAFT the author accepts/edits client-side:

  1. POST /courses/{course_id}/generate-objectives   → GeneratedObjectives
  2. POST /items/{item_id}/generate-content          → GeneratedItemContent
  3. POST /objectives/{objective_id}/generate-assessment → GeneratedAssessment

These are ADVISORY: the handlers do NOT write the generated draft into the
draft model — the author reviews/edits/accepts it (a later slice), and the
mandatory QA → approval → release gate still stands between any draft and an
active ``CurriculumVersion``.

This is a NEW router file (does not touch ``app/builder/router_advisor.py``).
It mirrors that router's patterns: the same author role gate, and a
``get_author_ai`` dependency that 503s without an API key and is overridable in
tests by passing a fake ``author_ai=`` — ZERO real Anthropic calls in CI.
Course / item / objective are loaded tenant-scoped, so a cross-org id is
invisible through RLS and surfaces as 404.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, CourseAuthorAI
from app.ai.deckgen import DeckGenerator
from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedDeck,
    GeneratedItemContent,
    GeneratedObjectives,
)
from app.builder.deck_generator import generate_deck_for_course
from app.auth.rbac import require_roles
from app.builder.course_generator import CourseBrief
from app.builder.generation_runner import SessionScope, run_generation
from app.builder.models import (
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.config import settings
from app.database import get_db, org_scoped_session
from app.models.generation_job import GenerationJob
from app.tenant import require_org

router = APIRouter(prefix="/api/v1/builder", tags=["builder-ai"])

# Mirror router_advisor.py / router_course.py — the same author tier drives all
# of the builder routers.
_AUTHOR_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class GenerateObjectivesRequest(BaseModel):
    """Body for generate-objectives. ``topic`` defaults to the course title/description."""

    topic: str | None = Field(
        default=None,
        description="Topic to draft objectives for. Defaults to the course title/description.",
    )
    count: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="How many objectives to draft. Falls back to the generator default.",
    )


class GenerationJobStarted(BaseModel):
    """202 response for POST /generate-course — the async job was scheduled.

    The heavy orchestration runs on a background task; poll
    ``GET /generate-course/jobs/{job_id}`` for progress and the resulting course.
    """

    job_id: uuid.UUID


class GenerationJobStatus(BaseModel):
    """Poll response for a course-generation job.

    ``course_id`` is set only when ``status == "complete"`` (the assembled,
    mutable DraftCourse the author drops into); ``error`` is set only when
    ``status == "failed"``. ``completed_steps``/``total_steps``/``phase`` drive
    the progress UI while ``status`` is ``pending``/``running``.
    """

    job_id: uuid.UUID
    status: str
    completed_steps: int
    total_steps: int
    phase: str | None = None
    course_id: uuid.UUID | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Dependency (503 without key; tests pass a fake directly)
# ---------------------------------------------------------------------------


def get_author_ai() -> CourseAuthorAI:
    """Lazily build the real governed AI client; 503 if the API key is not configured.

    Tests override this by passing a fake ``author_ai=`` kwarg to the handler —
    ZERO real network in CI. Mirrors ``router_advisor.get_categorizer`` and
    ``impact.get_impact_analyzer``.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI content generation is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


def get_generation_session_scope() -> SessionScope:
    """The session factory the background course-generation runner opens.

    Production: ``org_scoped_session`` — the runner runs OUTSIDE the request, so
    it must set the tenant ContextVar + ``app.current_org`` GUC on its own
    session (this factory does both, per transaction begin). Tests override this
    to yield an already-org-scoped session, so no real DB connection is opened on
    the app engine and cross-tenant isolation is still exercised.
    """
    return org_scoped_session


def _author_id(current: dict[str, Any]) -> uuid.UUID | None:
    """Best-effort parse of the JWT subject into a UUID author id."""
    try:
        return uuid.UUID(current["sub"])
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tenant-scoped loaders (cross-org id is invisible through RLS → 404)
# ---------------------------------------------------------------------------


async def _get_course(db: AsyncSession, course_id: uuid.UUID) -> DraftCourse:
    course = (
        await db.execute(select(DraftCourse).where(DraftCourse.id == course_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Draft course not found")
    return course


async def _get_item(db: AsyncSession, item_id: uuid.UUID) -> DraftItem:
    item = (
        await db.execute(select(DraftItem).where(DraftItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Draft item not found")
    return item


async def _get_objective(db: AsyncSession, objective_id: uuid.UUID) -> DraftObjective:
    obj = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.id == objective_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Draft objective not found")
    return obj


def _course_context(course: DraftCourse) -> str:
    """A compact, model-readable course-context string for grounding the generators."""
    lines = [f"COURSE: {course.title}"]
    if course.description:
        lines.append(f"DESCRIPTION: {course.description}")
    if course.target_weeks:
        lines.append(f"TARGET WEEKS: {course.target_weeks}")
    return "\n".join(lines)


async def _item_objective_text(db: AsyncSession, item: DraftItem) -> str:
    """Grounding objective(s) for an item's content: its linked objectives, joined.

    Falls back to a title-derived instruction when no objective is linked, so
    generation still has something concrete to aim at.
    """
    texts = (
        await db.execute(
            select(DraftObjective.text)
            .join(
                DraftItemObjective,
                DraftItemObjective.draft_objective_id == DraftObjective.id,
            )
            .where(DraftItemObjective.draft_item_id == item.id)
            .order_by(DraftObjective.order_index)
        )
    ).scalars().all()
    if texts:
        return "\n".join(f"- {t}" for t in texts)
    return (
        "(No objective is linked to this item. Infer a suitable objective from "
        f"the item title: {item.title})"
    )


# ---------------------------------------------------------------------------
# Endpoints (advisory — the generated draft is returned, never auto-written)
# ---------------------------------------------------------------------------


@router.post(
    "/courses/{course_id}/generate-objectives",
    response_model=GeneratedObjectives,
)
async def generate_objectives_endpoint(
    course_id: uuid.UUID,
    body: GenerateObjectivesRequest,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    author_ai: CourseAuthorAI = Depends(get_author_ai),
) -> GeneratedObjectives:
    """Draft Bloom-tagged objectives for a course. Advisory — not written into the draft.

    ``topic`` defaults to the course title (plus description, if any). Grounds the
    generator in the course's learner_profile.
    """
    course = await _get_course(db, course_id)

    topic = body.topic
    if not topic:
        topic = course.title
        if course.description:
            topic = f"{course.title} — {course.description}"

    learner_profile: dict = course.learner_profile or {}
    # Only pass count when the author specified one; otherwise use the generator default.
    kwargs: dict[str, Any] = {"topic": topic, "learner_profile": learner_profile}
    if body.count is not None:
        kwargs["count"] = body.count

    return await author_ai.generate_objectives(**kwargs)


@router.post(
    "/items/{item_id}/generate-content",
    response_model=GeneratedItemContent,
)
async def generate_content_endpoint(
    item_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    author_ai: CourseAuthorAI = Depends(get_author_ai),
) -> GeneratedItemContent:
    """Draft the body of one item, grounded in its kind + linked objective + course.

    Advisory — the draft is returned, never auto-written into the item.
    """
    item = await _get_item(db, item_id)
    course = await _get_course(db, item.draft_course_id)

    kind = item.kind.value if hasattr(item.kind, "value") else str(item.kind)
    objective = await _item_objective_text(db, item)

    return await author_ai.generate_item_content(
        objective=objective,
        kind=kind,
        course_context=_course_context(course),
    )


@router.post(
    "/objectives/{objective_id}/generate-assessment",
    response_model=GeneratedAssessment,
)
async def generate_assessment_endpoint(
    objective_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    author_ai: CourseAuthorAI = Depends(get_author_ai),
) -> GeneratedAssessment:
    """Draft an assessment + rubric for one objective, grounded in its course context.

    Advisory — the draft is returned, never auto-written into the draft.
    """
    objective = await _get_objective(db, objective_id)
    course = await _get_course(db, objective.draft_course_id)

    return await author_ai.generate_assessment(
        objective=objective.text,
        course_context=_course_context(course),
    )


@router.post(
    "/courses/{course_id}/generate-deck",
    response_model=GeneratedDeck,
)
async def generate_deck_endpoint(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    author_ai: DeckGenerator = Depends(get_author_ai),
) -> GeneratedDeck:
    """Author a standard-conforming Marp ``deck.md`` for a course. ADVISORY (D-2).

    The deck is fully AI-generated but a HUMAN reviews it before release — this
    endpoint RETURNS the generated deck for review and stores nothing (no
    migration). Grounded strictly in the course's objectives + content items;
    real visual quality is only proven once rendered via the S1 pipeline. A
    cross-org / unknown course id is invisible through RLS -> 404. 503 if no API
    key (via ``get_author_ai``).
    """
    deck = await generate_deck_for_course(db, course_id, author_ai)
    if deck is None:
        raise HTTPException(status_code=404, detail="Draft course not found")
    return deck


# ---------------------------------------------------------------------------
# Full-course orchestrator (the headline): brief -> complete mutable DraftCourse
# ---------------------------------------------------------------------------


@router.post(
    "/generate-course",
    response_model=GenerationJobStarted,
    status_code=202,
)
async def generate_course_endpoint(
    body: CourseBrief,
    background_tasks: BackgroundTasks,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    author_ai: CourseAuthorAI = Depends(get_author_ai),
    session_scope: SessionScope = Depends(get_generation_session_scope),
) -> GenerationJobStarted:
    """Schedule an async full-course generation from a brief; return 202 + job_id.

    The orchestration (``1 + 2 * objectives_count`` sequential ~29s AI calls) is
    far too long to hold a request open, so this endpoint only creates a
    ``GenerationJob`` (``pending``) and hands the work to a background task, then
    returns immediately. Clients poll ``GET /generate-course/jobs/{job_id}`` for
    live progress and the resulting course. ``objectives_count`` (<= 20, enforced
    by ``CourseBrief``) bounds the cost; ``objectives_count > 20`` is a 422 at
    body validation — no job is created. The background runner assembles a
    complete, MUTABLE ``DraftCourse`` (best-effort per item: a failed item is
    skipped and recorded as a durable advisor note, never failing the course).
    """
    author_id = _author_id(current)
    org_id = require_org()

    job = GenerationJob(
        status="pending",
        total_steps=1 + 2 * body.objectives_count,
        completed_steps=0,
        created_by=author_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(
        run_generation,
        job.id,
        body,
        author_id,
        org_id,
        author_ai=author_ai,
        session_scope=session_scope,
    )
    return GenerationJobStarted(job_id=job.id)


@router.get(
    "/generate-course/jobs/{job_id}",
    response_model=GenerationJobStatus,
)
async def get_generation_job_endpoint(
    job_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> GenerationJobStatus:
    """Poll one course-generation job. Tenant + owner scoped (else 404).

    A job from another tenant is invisible through RLS / the app-layer
    auto-filter; a job owned by another user in the same tenant is excluded by
    the ``created_by`` predicate — both surface as 404 rather than leaking
    existence.
    """
    job = (
        await db.execute(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.created_by == _author_id(current),
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")

    return GenerationJobStatus(
        job_id=job.id,
        status=job.status,
        completed_steps=job.completed_steps,
        total_steps=job.total_steps,
        phase=job.phase,
        course_id=job.course_id,
        error=job.error,
    )
