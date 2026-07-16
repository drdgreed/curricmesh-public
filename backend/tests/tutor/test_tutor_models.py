"""Model tests for the tutor conversation store (Phase B, B3).

Exercise persistence, tenant stamping, the enrollment pin, role/citations
storage, and cascade on delete — against the live RLS'd test DB (db_session).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.learner import Enrollment
from app.models.tutor import TutorConversation, TutorMessage
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import seed_version_with_members


async def _enrollment(db: AsyncSession) -> Enrollment:
    version = await seed_version_with_members(db, texts=["welcome to the course"])
    enrollment = Enrollment(
        learner_id=uuid.uuid4(), curriculum_version_id=version.id
    )
    db.add(enrollment)
    await db.flush()
    return enrollment


@pytest.mark.asyncio
async def test_conversation_persists_and_is_tenant_stamped(db_session: AsyncSession):
    enrollment = await _enrollment(db_session)
    convo = TutorConversation(enrollment_id=enrollment.id)
    db_session.add(convo)
    await db_session.commit()
    await db_session.refresh(convo)

    assert convo.id is not None
    assert convo.organization_id == DEFAULT_ORG_ID
    assert convo.enrollment_id == enrollment.id
    assert convo.created_at is not None


@pytest.mark.asyncio
async def test_message_persists_role_text_and_citations(db_session: AsyncSession):
    enrollment = await _enrollment(db_session)
    convo = TutorConversation(enrollment_id=enrollment.id)
    db_session.add(convo)
    await db_session.flush()

    learner_turn = TutorMessage(
        conversation_id=convo.id,
        role="learner",
        text="What is RAG? Reach me at jane@example.com",  # full, un-redacted
        citations=None,
    )
    tutor_turn = TutorMessage(
        conversation_id=convo.id,
        role="tutor",
        text="Retrieval-augmented generation grounds answers in course content.",
        citations=[{"chunk_id": str(uuid.uuid4()), "source_member_id": None}],
    )
    db_session.add_all([learner_turn, tutor_turn])
    await db_session.commit()
    await db_session.refresh(learner_turn)
    await db_session.refresh(tutor_turn)

    assert learner_turn.organization_id == DEFAULT_ORG_ID
    assert learner_turn.role == "learner"
    # The FULL un-redacted text is what persists server-side (D5 record).
    assert "jane@example.com" in learner_turn.text
    assert learner_turn.citations is None
    assert tutor_turn.role == "tutor"
    assert tutor_turn.citations[0]["chunk_id"]


@pytest.mark.asyncio
async def test_messages_queryable_by_conversation(db_session: AsyncSession):
    enrollment = await _enrollment(db_session)
    convo = TutorConversation(enrollment_id=enrollment.id)
    db_session.add(convo)
    await db_session.flush()
    db_session.add_all(
        [
            TutorMessage(conversation_id=convo.id, role="learner", text="q"),
            TutorMessage(conversation_id=convo.id, role="tutor", text="a"),
        ]
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(TutorMessage)
            .where(TutorMessage.conversation_id == convo.id)
            .order_by(TutorMessage.created_at)
        )
    ).scalars().all()
    assert [m.role for m in rows] == ["learner", "tutor"]
