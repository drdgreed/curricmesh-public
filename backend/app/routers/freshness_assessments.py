"""Router: gap assessments for the freshness pipeline (Monitor Queue).

GET /api/v1/freshness/assessments?recommendation=<optional filter>
  — list gap assessments ordered last_evaluated_at DESC (org-scoped via RLS)

Architect / program_manager only. Read-only v1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.models.freshness_pipeline import GapAssessment

router = APIRouter(prefix="/api/v1/freshness", tags=["freshness"])

_ASSESSMENT_ROLES = require_roles("architect", "program_manager")


# ---------------------------------------------------------------------------
# Pydantic models (inline — mirror freshness_watchlist style)
# ---------------------------------------------------------------------------


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    curriculum_id: uuid.UUID
    topic: str
    display_topic: str
    recommendation: str
    confidence: float
    scores: dict
    rationale: str
    dossier: list
    times_seen: int
    times_seen_at_last_eval: int
    promoted_ccr_id: uuid.UUID | None
    first_seen_at: datetime
    last_evaluated_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assessments", response_model=list[AssessmentOut])
async def list_assessments(
    recommendation: Literal["adopt_now", "monitor", "reject"] | None = None,
    current: dict[str, Any] = Depends(_ASSESSMENT_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[AssessmentOut]:
    stmt = select(GapAssessment).order_by(GapAssessment.last_evaluated_at.desc())
    if recommendation is not None:
        stmt = stmt.where(GapAssessment.recommendation == recommendation)
    result = await db.execute(stmt)
    return [AssessmentOut.model_validate(row) for row in result.scalars().all()]
