"""Acceptance tests for the ``answer`` tutor seam (Phase B, B3).

The load-bearing invariants, proven with explicit assertions:
  (a) grounded answer cites the seeded chunks;
  (b) empty retrieval -> refusal, and the model is NEVER called to fabricate;
  (c) D5 — the text handed to the model carries NO learner identity and the
      question is PII-redacted;
  (d) the FULL un-redacted turn is persisted server-side.

Uses a FakeEmbedder + FakeTutorAI: no real AI/embedding calls.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import FakeEmbedder
from app.core.tutor.answer import REFUSAL_TEXT, answer
from app.models.learner import Enrollment
from app.models.tutor import TutorConversation, TutorMessage
from app.ai.tutor import build_tutor_user_prompt, TUTOR_SYSTEM_PROMPT
from tests.tutor._helpers import FakeTutorAI, seed_enrollment_with_index


@pytest.mark.asyncio
async def test_grounded_answer_cites_seeded_chunks(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(
        db_session,
        texts=[
            "retrieval augmented generation grounds answers in course content",
            "vector databases store embeddings for nearest-neighbour search",
        ],
    )
    fake = FakeTutorAI(answer_text="RAG grounds answers in the course material.")
    # FakeEmbedder is deterministic but NON-semantic: only an exact-text query
    # lands at cosine distance 0, so query the exact chunk text to pin ordering.
    result = await answer(
        db_session,
        enrollment=enrollment,
        question="retrieval augmented generation grounds answers in course content",
        embedder=FakeEmbedder(),
        tutor_ai=fake,
    )

    assert result.text == "RAG grounds answers in the course material."
    assert fake.called
    # Cites at least one real chunk with a source_member_id.
    assert result.citations
    assert any(c.source_member_id is not None for c in result.citations)
    # The top citation snippet is the exact-match chunk (FakeEmbedder is
    # deterministic -> the matching chunk sorts first at cosine distance 0).
    assert "retrieval augmented generation" in result.citations[0].snippet


@pytest.mark.asyncio
async def test_empty_retrieval_refuses_without_calling_model(db_session: AsyncSession):
    # A released version with NO ingested chunks -> retrieval returns nothing.
    from tests.retrieval._helpers import seed_version_with_members

    version = await seed_version_with_members(db_session, texts=["unindexed body"])
    enrollment = Enrollment(
        learner_id=uuid.uuid4(), curriculum_version_id=version.id
    )
    db_session.add(enrollment)
    await db_session.flush()

    fake = FakeTutorAI()
    result = await answer(
        db_session,
        enrollment=enrollment,
        question="Tell me anything at all",
        embedder=FakeEmbedder(),
        tutor_ai=fake,
    )

    # Grounding gate: grounded refusal, NO citations, and the model was NEVER
    # asked to fabricate an answer from no context.
    assert result.text == REFUSAL_TEXT
    assert result.citations == []
    assert fake.called is False, "model must NOT be called on empty retrieval"


@pytest.mark.asyncio
async def test_d5_model_never_sees_identity_and_question_is_redacted(
    db_session: AsyncSession,
):
    learner_id = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session,
        texts=["agentic AI systems use tools and memory to accomplish goals"],
        learner_id=learner_id,
    )
    fake = FakeTutorAI()
    # A question laced with the learner's own PII.
    raw_q = "I'm learner jane.doe@example.com (call +1 555 123 4567): what is agentic AI?"
    await answer(
        db_session,
        enrollment=enrollment,
        question=raw_q,
        embedder=FakeEmbedder(),
        tutor_ai=fake,
    )

    assert fake.called
    call = fake.calls[0]
    # Redaction: the raw PII never reaches the model.
    assert "jane.doe@example.com" not in call.question
    assert "555" not in call.question
    assert "[EMAIL]" in call.question and "[PHONE]" in call.question
    # Identity separation: no learner id anywhere the model sees it.
    identity = str(learner_id)
    assert identity not in call.question
    assert all(identity not in chunk for chunk in call.context_chunks)
    # And the fully-composed prompt the real client would send is identity-free.
    prompt = build_tutor_user_prompt(call.question, call.context_chunks)
    assert identity not in prompt
    assert "jane.doe@example.com" not in prompt
    assert identity not in TUTOR_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_full_unredacted_turn_is_persisted(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["prompt engineering shapes model behaviour"]
    )
    fake = FakeTutorAI(answer_text="Prompt engineering shapes model behaviour.")
    raw_q = "email me at bob@corp.com — what is prompt engineering?"
    result = await answer(
        db_session,
        enrollment=enrollment,
        question=raw_q,
        embedder=FakeEmbedder(),
        tutor_ai=fake,
    )

    msgs = (
        await db_session.execute(
            select(TutorMessage)
            .where(TutorMessage.conversation_id == result.conversation_id)
            .order_by(TutorMessage.created_at)
        )
    ).scalars().all()
    assert [m.role for m in msgs] == ["learner", "tutor"]
    # The FULL un-redacted question is stored server-side (D5 record).
    assert msgs[0].text == raw_q
    assert "bob@corp.com" in msgs[0].text
    assert msgs[0].citations is None
    # The tutor turn carries the answer + citations.
    assert msgs[1].text == "Prompt engineering shapes model behaviour."
    assert msgs[1].citations and msgs[1].citations[0]["chunk_id"]

    # The conversation row exists and is pinned to the enrollment.
    convo = await db_session.get(TutorConversation, result.conversation_id)
    assert convo is not None
    assert convo.enrollment_id == enrollment.id


@pytest.mark.asyncio
async def test_session_language_reaches_model_and_prompt(db_session: AsyncSession):
    """T3b: the session language reaches the seam call + the composed prompt, and
    the default ``en`` is threaded unchanged when unspecified. D5 is unaffected —
    the language string carries no identity."""
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["agentic AI systems use tools and memory"]
    )
    fake = FakeTutorAI()
    await answer(
        db_session,
        enrollment=enrollment,
        question="agentic AI systems use tools and memory",
        embedder=FakeEmbedder(),
        tutor_ai=fake,
        language="Spanish",
    )
    assert fake.calls[-1].language == "Spanish"
    # The composed prompt the real client would send carries the reply instruction.
    prompt = build_tutor_user_prompt(
        fake.calls[-1].question, fake.calls[-1].context_chunks, "Spanish"
    )
    assert "Respond in Spanish." in prompt
    # Default (no language kwarg) threads "en" and leaves the prompt unchanged.
    await answer(
        db_session,
        enrollment=enrollment,
        question="agentic AI systems use tools and memory",
        embedder=FakeEmbedder(),
        tutor_ai=fake,
    )
    assert fake.calls[-1].language == "en"
    default_prompt = build_tutor_user_prompt(
        fake.calls[-1].question, fake.calls[-1].context_chunks
    )
    assert "Respond in" not in default_prompt


@pytest.mark.asyncio
async def test_session_language_does_not_relax_grounding_gate(db_session: AsyncSession):
    """T3b: a non-default language must NOT cause the model to be called on empty
    retrieval — the grounding gate still returns the refusal without a model call."""
    from tests.retrieval._helpers import seed_version_with_members

    version = await seed_version_with_members(db_session, texts=["unindexed body"])
    enrollment = Enrollment(learner_id=uuid.uuid4(), curriculum_version_id=version.id)
    db_session.add(enrollment)
    await db_session.flush()

    fake = FakeTutorAI()
    result = await answer(
        db_session,
        enrollment=enrollment,
        question="anything",
        embedder=FakeEmbedder(),
        tutor_ai=fake,
        language="Spanish",
    )
    assert result.text == REFUSAL_TEXT
    assert fake.called is False, "language must not relax the grounding gate"


@pytest.mark.asyncio
async def test_existing_conversation_is_reused(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["fine-tuning adapts a base model to a narrow task"]
    )
    fake = FakeTutorAI()
    convo = TutorConversation(enrollment_id=enrollment.id)
    db_session.add(convo)
    await db_session.flush()

    r1 = await answer(
        db_session, enrollment=enrollment, question="q1 fine-tuning",
        embedder=FakeEmbedder(), tutor_ai=fake, conversation=convo,
    )
    r2 = await answer(
        db_session, enrollment=enrollment, question="q2 fine-tuning",
        embedder=FakeEmbedder(), tutor_ai=fake, conversation=convo,
    )
    assert r1.conversation_id == r2.conversation_id == convo.id
    # 2 turns per ask (learner + tutor) -> 4 messages in the one thread.
    msgs = (
        await db_session.execute(
            select(TutorMessage).where(TutorMessage.conversation_id == convo.id)
        )
    ).scalars().all()
    assert len(msgs) == 4
