"""Router: RAG Q&A tutor (Phase B, B3) — learner-facing, enrollment-scoped.

Endpoints (learner-role gated; all tenant + enrollment scoped — cross-tenant /
cross-learner ids surface as 404):

  POST /learn/tutor/{enrollment_id}/ask
      {question, conversation_id?} -> {answer, citations, conversation_id}
  GET  /learn/tutor/{enrollment_id}/conversations/{cid}
      -> the full conversation history (server-side record).

The chat UI is a later slice (B6); this is the backend seam. All grounding + D5
anonymization lives in ``app.core.tutor.answer`` — this router only resolves the
caller's enrollment/conversation (RLS-scoped) and commits the turn.

The embedder is config-chosen (``get_embedder`` -> FakeEmbedder in dev/CI). The
governed tutor model is injected at ``get_tutor_ai`` (503 without an API key;
tests override with a fake -> ZERO real Anthropic calls in CI), mirroring
``authoring_ai.get_author_ai``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.tutor import Tutor
from app.auth.rbac import require_roles
from app.config import settings
from app.core.retrieval.embedder import get_embedder
from app.core.tutor.answer import answer as run_answer
from app.core.tutor.assess import assess as run_assess
from app.core.tutor.coach import coach as run_coach
from app.database import get_db
from app.models.learner import AssessmentSubmission
from app.models.tutor import TutorConversation, TutorMessage
from app.routers.learn import _caller_learner_id, _load_owned_enrollment

router = APIRouter(prefix="/api/v1/learn/tutor", tags=["tutor"])

_LEARNER = require_roles("learner")


def get_tutor_ai() -> Tutor:
    """Build the real governed tutor client; 503 if the API key is missing.

    Tests override this seam with a fake -> ZERO real Anthropic calls in CI.
    Mirrors ``authoring_ai.get_author_ai``.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI tutor is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str
    conversation_id: uuid.UUID | None = None
    # T3b — the learner's session-chosen reply language (session/client-held,
    # sent per request; NOT persisted). Default English preserves behaviour.
    language: str = "en"


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    source_member_id: uuid.UUID | None
    snippet: str


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    conversation_id: uuid.UUID


class CoachRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    # T3b — session-chosen reply language (see AskRequest.language).
    language: str = "en"


class CoachResponse(BaseModel):
    message: str
    citations: list[CitationOut]
    conversation_id: uuid.UUID


class AssessResponse(BaseModel):
    submission_id: uuid.UUID
    score: float
    feedback: str


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    citations: list[dict] | None
    created_at: datetime


class ConversationOut(BaseModel):
    conversation_id: uuid.UUID
    enrollment_id: uuid.UUID
    messages: list[MessageOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_owned_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, enrollment_id: uuid.UUID
) -> TutorConversation:
    """Load a conversation that belongs to *enrollment_id*, or 404.

    Tenant scope (auto-filter) + the ``enrollment_id`` predicate collapse to a
    single 404 — a learner can never observe another enrollment's thread.
    """
    convo = (
        await db.execute(
            select(TutorConversation).where(
                TutorConversation.id == conversation_id,
                TutorConversation.enrollment_id == enrollment_id,
            )
        )
    ).scalar_one_or_none()
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


async def _load_owned_submission(
    db: AsyncSession, submission_id: uuid.UUID, enrollment_id: uuid.UUID
) -> AssessmentSubmission:
    """Load a submission that belongs to *enrollment_id*, or 404.

    Tenant scope (auto-filter) + the ``enrollment_id`` predicate collapse to a
    single 404 — a learner can never assess another enrollment's submission.
    """
    sub = (
        await db.execute(
            select(AssessmentSubmission).where(
                AssessmentSubmission.id == submission_id,
                AssessmentSubmission.enrollment_id == enrollment_id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{enrollment_id}/ask", response_model=AskResponse)
async def ask(
    enrollment_id: uuid.UUID,
    body: AskRequest,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    tutor_ai: Tutor = Depends(get_tutor_ai),
) -> AskResponse:
    """Ask the tutor a question, grounded in the enrolled version's content."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)

    conversation = None
    if body.conversation_id is not None:
        conversation = await _load_owned_conversation(
            db, body.conversation_id, enrollment.id
        )

    result = await run_answer(
        db,
        enrollment=enrollment,
        question=body.question,
        embedder=get_embedder(),
        tutor_ai=tutor_ai,
        conversation=conversation,
        language=body.language,
    )
    await db.commit()

    return AskResponse(
        answer=result.text,
        conversation_id=result.conversation_id,
        citations=[
            CitationOut(
                chunk_id=c.chunk_id,
                source_member_id=c.source_member_id,
                snippet=c.snippet,
            )
            for c in result.citations
        ],
    )


@router.post("/{enrollment_id}/coach", response_model=CoachResponse)
async def coach(
    enrollment_id: uuid.UUID,
    body: CoachRequest,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    tutor_ai: Tutor = Depends(get_tutor_ai),
) -> CoachResponse:
    """Proactive next-step coaching grounded in the learner's progress (B4)."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)

    conversation = None
    if body.conversation_id is not None:
        conversation = await _load_owned_conversation(
            db, body.conversation_id, enrollment.id
        )

    result = await run_coach(
        db,
        enrollment=enrollment,
        embedder=get_embedder(),
        tutor_ai=tutor_ai,
        conversation=conversation,
        language=body.language,
    )
    await db.commit()

    return CoachResponse(
        message=result.text,
        conversation_id=result.conversation_id,
        citations=[
            CitationOut(
                chunk_id=c.chunk_id,
                source_member_id=c.source_member_id,
                snippet=c.snippet,
            )
            for c in result.citations
        ],
    )


@router.post(
    "/{enrollment_id}/submissions/{submission_id}/assess",
    response_model=AssessResponse,
)
async def assess(
    enrollment_id: uuid.UUID,
    submission_id: uuid.UUID,
    language: str = "en",
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    tutor_ai: Tutor = Depends(get_tutor_ai),
) -> AssessResponse:
    """Score + coach a learner's assessment submission against its rubric (B5).

    ``language`` (T3b) is a session-held query param localizing only the feedback
    text; the numeric score is language-agnostic. Default English is unchanged.
    """
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    submission = await _load_owned_submission(db, submission_id, enrollment.id)

    result = await run_assess(
        db, submission=submission, tutor_ai=tutor_ai, language=language
    )
    await db.commit()

    return AssessResponse(
        submission_id=submission.id,
        score=result.score,
        feedback=result.feedback,
    )


@router.get(
    "/{enrollment_id}/conversations/{conversation_id}",
    response_model=ConversationOut,
)
async def conversation_history(
    enrollment_id: uuid.UUID,
    conversation_id: uuid.UUID,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """The full server-side record of a tutor conversation (D5 record)."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    convo = await _load_owned_conversation(db, conversation_id, enrollment.id)

    rows = (
        await db.execute(
            select(TutorMessage)
            .where(TutorMessage.conversation_id == convo.id)
            .order_by(TutorMessage.created_at)
        )
    ).scalars().all()

    return ConversationOut(
        conversation_id=convo.id,
        enrollment_id=convo.enrollment_id,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                text=m.text,
                citations=m.citations,
                created_at=m.created_at,
            )
            for m in rows
        ],
    )
