"""Router: /api/v1/analytics — change-velocity & time-in-state read aggregates (V3-A).

Thin HTTP layer over app/services/analytics.py (the pure engine). Read-gated to
``architect``, ``program_manager``, ``qa_lead`` via ``require_roles``. Registered
in main.py inside the tenant_context router group, so every query is org-scoped
by the same auto-filter / RLS path the other domain routers use.

Optional ``?curriculum_id=`` narrows each aggregate to a single curriculum.
``?bucket=`` selects the velocity granularity (``week`` default, or ``month``).
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.services import analytics
from app.schemas.analytics import (
    AnalyticsOverview,
    CadenceSummary,
    StateDuration,
    VelocityBucket,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_READ_ROLES = require_roles("architect", "program_manager", "qa_lead")


@router.get("/overview", response_model=AnalyticsOverview)
async def get_overview(
    curriculum_id: uuid.UUID | None = None,
    bucket: Literal["week", "month"] = "week",
    current: dict[str, Any] = Depends(_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOverview:
    """Composed dashboard payload: velocity, time-in-state, cadence, distribution."""
    return await analytics.overview(db, curriculum_id=curriculum_id, bucket=bucket)


@router.get("/velocity", response_model=list[VelocityBucket])
async def get_velocity(
    curriculum_id: uuid.UUID | None = None,
    bucket: Literal["week", "month"] = "week",
    current: dict[str, Any] = Depends(_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[VelocityBucket]:
    """CCRs opened and versions released per time bucket."""
    return await analytics.change_velocity(db, curriculum_id=curriculum_id, bucket=bucket)


@router.get("/time-in-state", response_model=list[StateDuration])
async def get_time_in_state(
    curriculum_id: uuid.UUID | None = None,
    current: dict[str, Any] = Depends(_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[StateDuration]:
    """Per-lifecycle-state mean & median dwell time (n=0 when too sparse)."""
    return await analytics.time_in_state(db, curriculum_id=curriculum_id)


@router.get("/cadence", response_model=CadenceSummary)
async def get_cadence(
    curriculum_id: uuid.UUID | None = None,
    current: dict[str, Any] = Depends(_READ_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CadenceSummary:
    """Release count and mean/median days between consecutive releases."""
    return await analytics.release_cadence(db, curriculum_id=curriculum_id)
