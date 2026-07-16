"""Router: POST /api/v1/curricula/{curriculum_id}/impact — AI CCR-impact guidance.

At authoring time, an author POSTs a proposed change-set and Claude estimates
its impact on learning objectives, instructional duration, and student cognitive
load. The report is ADVISORY — it informs the author; it does not gate anything.

Two modes:
  * ``ccr_id`` omitted → stateless PREVIEW: analyze and return, no DB write.
  * ``ccr_id`` provided → persist the report onto that CCR's ``impact`` JSONB
    (``model_dump(mode="json")``) and commit, so the analysis is saved on the
    change request.

Testability mirrors qa.py exactly: ``get_impact_analyzer`` raises 503 if no API
key is configured; tests override it with a fake — ZERO real network in CI. The
analyzer does the LLM work; this router stays thin.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.impact import ImpactAnalyzer, score_impact
from app.ai.schemas import ImpactReport
from app.auth.rbac import require_roles
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.workflow import ChangeRequest
from app.schemas.release import ReleaseChangeSet

router = APIRouter(prefix="/api/v1/curricula", tags=["impact"])

# Authoring roles — mirror ccr.py's _SUBMIT_ROLES (the people who draft CCRs).
_IMPACT_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)


class ImpactRequest(BaseModel):
    """Body for the impact endpoint: a change-set + optional authoring context."""

    change_set: ReleaseChangeSet
    title: str | None = None
    rationale: str | None = None
    # When set, persist the report onto this CCR's impact JSONB; else stateless.
    ccr_id: uuid.UUID | None = None


def get_impact_analyzer() -> ImpactAnalyzer:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency with a fake analyzer — ZERO real network in
    CI. Mirrors qa.py's get_ai_judge.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI impact analysis is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


async def _build_context(db: AsyncSession, curriculum_id: uuid.UUID) -> str | None:
    """Light curriculum context: the active version's section/week structure.

    Cheap to include and grounds the analyst in what already exists. Returns
    ``None`` if the curriculum has no active manifest yet (a fresh curriculum).
    """
    cv = await active_curriculum_version(db, curriculum_id)
    if cv is None:
        return None
    members = await version_members(db, cv.id)
    if not members:
        return f"Active version {cv.major}.{cv.minor}.{cv.patch} (no members)."
    lines = [
        f"Active version {cv.major}.{cv.minor}.{cv.patch} — {len(members)} assets:"
    ]
    for m in members:
        kind = m.kind.value if hasattr(m.kind, "value") else m.kind
        lines.append(
            f"  - {m.lineage_key} (kind: {kind}, week {m.week_index}, section {m.section!r})"
        )
    return "\n".join(lines)


@router.post("/{curriculum_id}/impact", response_model=ImpactReport)
async def estimate_impact(
    curriculum_id: uuid.UUID,
    body: ImpactRequest,
    current: dict[str, Any] = Depends(_IMPACT_ROLES),
    db: AsyncSession = Depends(get_db),
    analyzer: ImpactAnalyzer = Depends(get_impact_analyzer),
) -> ImpactReport:
    """Estimate a change-set's impact. Advisory. Persists onto the CCR if given."""
    curriculum = (
        await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    ).scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    context = await _build_context(db, curriculum_id)

    report = await score_impact(
        analyzer=analyzer,
        change_set=body.change_set,
        title=body.title,
        rationale=body.rationale,
        context=context,
    )

    # Persist onto the CCR's impact JSONB when a ccr_id is supplied. The CCR must
    # belong to the curriculum in the URL — the analysis was built against that
    # curriculum's context, so writing it onto a different curriculum's CCR would
    # store a misleading report.
    if body.ccr_id is not None:
        ccr = (
            await db.execute(
                select(ChangeRequest).where(
                    ChangeRequest.id == body.ccr_id,
                    ChangeRequest.curriculum_id == curriculum_id,
                )
            )
        ).scalar_one_or_none()
        if ccr is None:
            raise HTTPException(
                status_code=404,
                detail="CCR not found for this curriculum",
            )
        ccr.impact = report.model_dump(mode="json")
        db.add(ccr)
        await db.commit()

    return report
