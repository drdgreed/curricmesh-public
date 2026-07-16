"""Core tests for the ``assess`` orchestrator (Phase B, B5).

Load-bearing invariants asserted here:
* the score + feedback are WRITTEN BACK onto the submission;
* the item's rubric (from ``ContentVersion.metadata_``) + prompt reach the model;
* D5 — the raw response is stored server-side, only the PII-redacted response
  reaches the model, and no learner identity is ever passed;
* a missing rubric degrades gracefully (empty rubric; still scores).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tutor.assess import assess
from tests.tutor._helpers import (
    FakeTutorAI,
    seed_assessment_submission,
    seed_enrollment_with_index,
)


@pytest.mark.asyncio
async def test_assess_writes_score_and_feedback(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(db_session, texts=["course"])
    submission = await seed_assessment_submission(
        db_session,
        enrollment=enrollment,
        response_text="RAG retrieves chunks then generates a grounded answer.",
        rubric="Criterion 1: defines RAG. Criterion 2: mentions grounding.",
    )
    fake = FakeTutorAI(assess_score=0.9, assess_feedback="Great — hits both criteria.")

    result = await assess(db_session, submission=submission, tutor_ai=fake)

    # Score + feedback written back onto the submission (the point of B5).
    assert submission.score == 0.9
    assert submission.feedback == "Great — hits both criteria."
    assert result.score == 0.9 and result.feedback == "Great — hits both criteria."

    # The rubric + prompt reached the model.
    call = fake.assess_calls[0]
    assert "Criterion 1" in call.rubric
    assert "retrieval-augmented generation" in call.assessment_prompt.lower()


@pytest.mark.asyncio
async def test_assess_is_d5_redacted_and_stores_raw(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["course"], learner_id=learner
    )
    raw = "My answer — reach me at jane.doe@example.com or 555-123-4567."
    submission = await seed_assessment_submission(
        db_session,
        enrollment=enrollment,
        response_text=raw,
        rubric="Explain clearly.",
    )
    fake = FakeTutorAI()
    await assess(db_session, submission=submission, tutor_ai=fake)

    # Raw response is preserved server-side (secure record).
    assert submission.response_text == raw

    # Only the REDACTED response reaches the model — no email/phone, no identity.
    sent = fake.assess_calls[0].response
    assert "jane.doe@example.com" not in sent
    assert "555-123-4567" not in sent
    assert "[EMAIL]" in sent and "[PHONE]" in sent
    assert str(learner) not in sent
    assert str(enrollment.id) not in sent


@pytest.mark.asyncio
async def test_assess_missing_rubric_degrades(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(db_session, texts=["course"])
    submission = await seed_assessment_submission(
        db_session,
        enrollment=enrollment,
        response_text="An answer.",
        rubric=None,  # no rubric authored
    )
    fake = FakeTutorAI(assess_score=0.5, assess_feedback="No rubric; graded on prompt.")

    result = await assess(db_session, submission=submission, tutor_ai=fake)

    assert fake.assess_calls[0].rubric == ""
    assert submission.score == 0.5
    assert result.feedback == "No rubric; graded on prompt."
