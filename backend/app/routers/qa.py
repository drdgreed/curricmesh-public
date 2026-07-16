"""Router: /api/v1/ccrs/{id}/qa — QA Review submission."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.qa_judge import QAJudge, score_qa
from app.auth.rbac import require_roles
from app.config import settings
from app.core.history import EventType
from app.core.workflow.engine import record_qa
from app.database import get_db
from app.integrations.notifier import notify
from app.models.workflow import ChangeRequest
from app.schemas.workflow import QAReviewCreate, QAReviewOut

router = APIRouter(prefix="/api/v1/ccrs", tags=["qa"])

_QA_ROLES = require_roles("qa_lead", "architect")


def get_ai_judge() -> QAJudge:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Defined locally (not reusing research.py's get_ai_extractor) so C3 adds the
    AI-judge endpoint without churning C2's passing tests. Tests override this
    dependency with a fake judge — ZERO real network in CI.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI QA review is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


@router.post("/{ccr_id}/qa", response_model=QAReviewOut, status_code=201)
async def submit_qa_review(
    ccr_id: uuid.UUID,
    body: QAReviewCreate,
    current: dict[str, Any] = Depends(_QA_ROLES),
    db: AsyncSession = Depends(get_db),
) -> QAReviewOut:
    """Submit a QA review. WorkflowError → 400 via central handler."""
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
    ccr = result.scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    reviewer_id: uuid.UUID | None = None
    try:
        reviewer_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    qa_review = await record_qa(
        db,
        ccr=ccr,
        reviewer_id=reviewer_id,
        dimension_scores=body.dimension_scores,
        verdict=body.verdict,
    )
    await db.commit()
    await db.refresh(qa_review)

    # Notify on pass AFTER commit — notification failure must NOT roll back the transaction.
    if body.verdict == "pass":
        await notify(
            EventType.qa_passed,
            {
                "ccr_id": str(ccr_id),
                "qa_review_id": str(qa_review.id),
                "verdict": body.verdict,
            },
        )

    return QAReviewOut.model_validate(qa_review)


@router.post("/{ccr_id}/qa/ai-review", response_model=QAReviewOut, status_code=201)
async def ai_review(
    ccr_id: uuid.UUID,
    current: dict[str, Any] = Depends(_QA_ROLES),
    db: AsyncSession = Depends(get_db),
    judge: QAJudge = Depends(get_ai_judge),
) -> QAReviewOut:
    """Draft an AI QA review (verdict='ai_draft') a human QA Lead then reviews.

    Advisory only: the ai_draft verdict can never satisfy the release gate.
    """
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
    ccr = result.scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    qa = await score_qa(db, ccr=ccr, judge=judge)
    await db.commit()
    await db.refresh(qa)
    return QAReviewOut.model_validate(qa)
