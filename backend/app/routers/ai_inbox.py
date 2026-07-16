"""Router: /api/v1/ai/inbox — AI-findings inbox (C5).

Surfaces two classes of inert advisory AI artifacts for human review:
  - AI-drafted CCRs (real ``draft`` ChangeRequests authored by the system AI
    Researcher) — read-only; the normal CCR→QA→approval flow already owns them.
  - AI-draft QA reviews (``verdict='ai_draft'``) — never count toward release; a
    human qa_lead/architect promotes them via the existing POST /ccrs/{id}/qa.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.qa_judge import AI_DRAFT_VERDICT
from app.auth.rbac import require_roles
from app.core.actors import get_ai_researcher
from app.database import get_db
from app.models.enums import LifecycleStatus
from app.models.workflow import ChangeRequest, QAReview
from app.schemas.workflow import AIDraftQAOut, AIInboxOut, CCROut

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/inbox", response_model=AIInboxOut)
async def ai_inbox(
    current: dict[str, Any] = Depends(
        require_roles(
            "architect",
            "program_manager",
            "instructor_lead",
            "instructor",
            "qa_lead",
            "devops",
        )
    ),
    db: AsyncSession = Depends(get_db),
) -> AIInboxOut:
    """List AI-drafted CCRs and AI-draft QA reviews for human triage."""
    # --- AI-drafted CCRs (authored by the system AI Researcher, still draft) ---
    ai = await get_ai_researcher(db)
    drafted_ccrs: list[CCROut] = []
    if ai is not None:
        ccr_result = await db.execute(
            select(ChangeRequest)
            .where(
                ChangeRequest.author_id == ai.id,
                ChangeRequest.status == LifecycleStatus.draft,
            )
            .order_by(ChangeRequest.created_at.desc())
        )
        drafted_ccrs = [
            CCROut.model_validate(c) for c in ccr_result.scalars().all()
        ]

    # --- AI-draft QA reviews (inert; joined to their CCR title) ---
    # QAReview.ccr_id is NOT NULL with ondelete=CASCADE, so the CCR always
    # exists — an inner join is the correct semantic (a QA draft can never be
    # orphaned) and guarantees ccr_title is populated.
    qa_result = await db.execute(
        select(QAReview, ChangeRequest.title)
        .join(ChangeRequest, QAReview.ccr_id == ChangeRequest.id)
        .where(QAReview.verdict == AI_DRAFT_VERDICT)
        .order_by(QAReview.created_at.desc())
    )
    draft_qa_reviews = [
        AIDraftQAOut(
            id=qa.id,
            ccr_id=qa.ccr_id,
            ccr_title=title,
            dimension_scores=qa.dimension_scores,
            evidence=qa.evidence,
            created_at=qa.created_at,
        )
        for qa, title in qa_result.all()
    ]

    return AIInboxOut(drafted_ccrs=drafted_ccrs, draft_qa_reviews=draft_qa_reviews)
