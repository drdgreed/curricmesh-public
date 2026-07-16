"""Router: effort + overload endpoints for a DraftCourse (Tasks 5 + 6).

Both endpoints are read-only: they compute on-the-fly from the course's stored
items, objectives, and effort_config — nothing is mutated.  They are gated by
the same author-role set as ``router_course`` and live under the same
``/api/v1/builder`` prefix.

GET /api/v1/builder/courses/{id}/effort
  Returns ``course_effort(…)`` — per-week student minutes + totals.

GET /api/v1/builder/courses/{id}/overload
  Returns ``week_flags(…)`` — overload + density warning per week.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.builder.compile import (
    AlreadyPublishedError,
    CompileValidationError,
    DraftNotFoundError,
    publish_draft,
)
from app.builder.effort import DEFAULT_RATES, course_effort
from app.builder.load import HARD_KINDS, week_flags
from app.builder.models import DraftCourse, DraftItem, DraftObjective
from app.core.manifest import version_edges, version_members
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/v1/builder", tags=["builder"])

_AUTHOR_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)

_DEFAULT_WEEKLY_HOURS = 10.0


async def _load_course(db: AsyncSession, course_id: uuid.UUID) -> DraftCourse:
    """Load a DraftCourse by id (tenant-scoped) or raise 404."""
    course = (
        await db.execute(select(DraftCourse).where(DraftCourse.id == course_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Draft course not found")
    return course


def _merge_rates(effort_config: dict | None) -> dict[str, float]:
    """Return DEFAULT_RATES updated with any per-key overrides from effort_config."""
    merged = dict(DEFAULT_RATES)
    if effort_config:
        for key in DEFAULT_RATES:
            if key in effort_config:
                merged[key] = float(effort_config[key])
    return merged


@router.get("/courses/{course_id}/effort")
async def get_effort(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compute and return student effort aggregated by week.

    Response shape::

        {
            "by_week": {
                "1": {"student_minutes": 145, "item_count": 2},
                "0": {"student_minutes": 10,  "item_count": 1}   // unscheduled
            },
            "total_student_minutes": 155
        }

    Week keys are serialised as strings by JSON (dict keys must be strings in
    JSON).  Week ``"0"`` is the *unscheduled* bucket (items with
    ``week_index=None``).  All other weeks start at 1.
    """
    course = await _load_course(db, course_id)
    rates = _merge_rates(course.effort_config)

    items = (
        await db.execute(
            select(DraftItem).where(DraftItem.draft_course_id == course_id)
        )
    ).scalars().all()

    objectives = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.draft_course_id == course_id)
        )
    ).scalars().all()

    return course_effort(items, objectives, rates)


@router.get("/courses/{course_id}/overload")
async def get_overload(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Compute and return overload + concept-density flags per week.

    Response shape::

        [
            {
                "week": 1,
                "student_hours": 10.0,
                "overload": true,
                "new_concepts": 5,
                "density_warn": true
            },
            ...
        ]

    Sorted ascending by week.  The ``weekly_hours_target`` is taken from
    ``course.learner_profile["weekly_hours_target"]``; defaults to 10.0
    if unset.
    """
    course = await _load_course(db, course_id)
    rates = _merge_rates(course.effort_config)

    # Weekly hours target from the learner profile.
    profile = course.learner_profile or {}
    weekly_target: float = float(
        profile.get("weekly_hours_target") or _DEFAULT_WEEKLY_HOURS
    )

    items = (
        await db.execute(
            select(DraftItem).where(DraftItem.draft_course_id == course_id)
        )
    ).scalars().all()

    objectives = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.draft_course_id == course_id)
        )
    ).scalars().all()

    effort = course_effort(items, objectives, rates)
    effort_by_week = effort["by_week"]

    # Build week→count mappings for objectives and items.
    objectives_by_week: dict[int, int] = {}
    for obj in objectives:
        w = obj.week_index if obj.week_index is not None else 0
        objectives_by_week[w] = objectives_by_week.get(w, 0) + 1

    # For items_by_week we pass the actual item lists so week_flags can filter
    # by HARD_KINDS itself — giving the caller the most accurate density count.
    items_by_week: dict[int, list] = {}
    for item in items:
        w = item.week_index if item.week_index is not None else 0
        items_by_week.setdefault(w, []).append(item)

    return week_flags(
        effort_by_week=effort_by_week,
        objectives_by_week=objectives_by_week,
        items_by_week=items_by_week,
        weekly_hours_target=weekly_target,
    )


@router.post("/courses/{course_id}/publish", status_code=201)
async def publish_course(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Submit a draft course for review — assemble a pre-active candidate version.

    Slice 5 (mandatory QA -> release): this NO LONGER activates the course. It
    assembles the complete immutable ``CurriculumVersion`` with a **candidate**
    status (``review``) and opens an **initial-release ``ChangeRequest``**. The
    course becomes active only after that CCR clears the 6-dimension QA + approval
    gate (``POST /api/v1/ccrs/{ccr_id}/qa`` · ``/approvals`` · ``/release``).

    Calls :func:`app.builder.compile.publish_draft` (which runs inside a SAVEPOINT
    and never commits — this router owns the transaction boundary) and commits on
    success. Domain failures map to HTTP:

    * unknown draft           → 404
    * already-submitted draft → 409
    * invalid manifest        → 422 (a prerequisite cycle or a dangling edge —
      fail-closed; nothing is persisted)

    Response shape::

        {
            "curriculum_id": "...",
            "version_id": "...",
            "ccr_id": "...",
            "semver": "1.0.0",
            "status": "review",
            "active": false,
            "member_count": 5,
            "edge_count": 2
        }
    """
    author_id: uuid.UUID | None = None
    try:
        author_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError, TypeError):
        pass

    # The initial-release CCR's author_id FKs users.id (nullable, ondelete SET
    # NULL). Only stamp it when the token's subject is a real user in this org —
    # otherwise leave it NULL rather than fail-close a publish on a stale/synthetic
    # subject. In production the JWT subject is always a live user.
    if author_id is not None:
        exists = await db.scalar(select(User.id).where(User.id == author_id))
        if exists is None:
            author_id = None

    try:
        result = await publish_draft(db, course_id, author_id=author_id)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlreadyPublishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CompileValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cv = result.version
    members = await version_members(db, cv.id)
    edges = await version_edges(db, cv.id)
    semver = f"{cv.major}.{cv.minor}.{cv.patch}"

    await db.commit()

    return {
        "curriculum_id": str(cv.curriculum_id),
        "version_id": str(cv.id),
        "ccr_id": str(result.ccr.id),
        "semver": semver,
        "status": cv.status.value,
        "active": False,
        "member_count": len(members),
        "edge_count": len(edges),
    }
