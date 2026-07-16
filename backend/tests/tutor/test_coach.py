"""Core tests for the ``coach`` orchestrator (Phase B, B4).

Load-bearing invariants asserted here:
* the learner's PROGRESS signal (counts + next-item label) reaches the model;
* D5 — no learner identity (id) ever reaches the prompt (progress or excerpts);
* course specifics are grounded in the next objective's retrieved chunks;
* an empty index degrades gracefully (model still coaches from progress; no
  citations; no fabrication path) rather than hard-refusing;
* the coaching turn is persisted to the tutor conversation store.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import FakeEmbedder
from app.core.tutor.coach import coach
from app.models.learner import Enrollment
from app.models.tutor import TutorConversation, TutorMessage
from tests.retrieval._helpers import seed_version_with_members
from tests.tutor._helpers import (
    FakeTutorAI,
    mark_item_complete,
    seed_enrollment_with_index,
)


@pytest.mark.asyncio
async def test_coach_uses_progress_and_grounds_and_persists(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session,
        texts=["intro to agents", "retrieval grounding", "evaluation harness"],
        learner_id=learner,
    )
    # Complete the first item → next objective is item at order 1.
    await mark_item_complete(db_session, enrollment, order=0)

    fake = FakeTutorAI(coach_text="Nice work on the intro — try the grounding lesson.")
    result = await coach(
        db_session, enrollment=enrollment, embedder=FakeEmbedder(), tutor_ai=fake
    )

    # The model was called exactly once, and the PROGRESS signal reached it.
    assert len(fake.coach_calls) == 1
    call = fake.coach_calls[0]
    assert "1 of 3" in call.progress
    assert "wk01/lesson_plan" in call.progress  # the next item's label

    # Grounding: the next objective's chunks reached the model as excerpts.
    assert call.context_chunks, "expected retrieved course excerpts"

    # Persisted: a conversation + a tutor coaching message with citations.
    assert result.text == "Nice work on the intro — try the grounding lesson."
    assert result.citations
    msgs = (
        await db_session.execute(
            select(TutorMessage).where(
                TutorMessage.conversation_id == result.conversation_id
            )
        )
    ).scalars().all()
    assert [m.role for m in msgs] == ["tutor"]
    assert msgs[0].citations


@pytest.mark.asyncio
async def test_coach_is_d5_anonymized(db_session: AsyncSession):
    """No learner identity may appear in the progress signal or the excerpts."""
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["alpha", "beta"], learner_id=learner
    )
    fake = FakeTutorAI()
    await coach(db_session, enrollment=enrollment, embedder=FakeEmbedder(), tutor_ai=fake)

    call = fake.coach_calls[0]
    haystack = call.progress + "\n" + "\n".join(call.context_chunks)
    assert str(learner) not in haystack
    assert str(enrollment.id) not in haystack


@pytest.mark.asyncio
async def test_coach_empty_index_degrades_gracefully(db_session: AsyncSession):
    """A released version with NO ingested chunks still coaches (no refusal)."""
    learner = uuid.uuid4()
    version = await seed_version_with_members(db_session, texts=["unindexed lesson"])
    enrollment = Enrollment(learner_id=learner, curriculum_version_id=version.id)
    db_session.add(enrollment)
    await db_session.flush()

    fake = FakeTutorAI(coach_text="Let's get started on your first lesson.")
    result = await coach(
        db_session, enrollment=enrollment, embedder=FakeEmbedder(), tutor_ai=fake
    )

    # Model IS called (progress is real grounding) but with no excerpts.
    assert len(fake.coach_calls) == 1
    assert fake.coach_calls[0].context_chunks == []
    assert result.citations == []
    assert result.text == "Let's get started on your first lesson."


@pytest.mark.asyncio
async def test_coach_all_complete_wraps_up(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["only lesson"], learner_id=learner
    )
    await mark_item_complete(db_session, enrollment, order=0)

    fake = FakeTutorAI(coach_text="You've finished — great job!")
    await coach(db_session, enrollment=enrollment, embedder=FakeEmbedder(), tutor_ai=fake)

    call = fake.coach_calls[0]
    assert "1 of 1" in call.progress
    assert "complete" in call.progress.lower()
    # No next objective → nothing retrieved.
    assert call.context_chunks == []
