"""API tests for the RAG tutor router (Phase B, B3).

Transport mirrors tests/learner/test_learn_api.py: an ASGITransport AsyncClient
with get_db + get_tutor_ai overridden (a FAKE tutor -> ZERO real Anthropic calls
in CI), and JWTs minted via create_access_token carrying the org claim.

Covers: role gating (learner vs non-learner -> 403), happy-path ask + citations,
conversation history round-trip, empty-retrieval refusal, cross-learner + cross-
tenant isolation (-> 404), and a conversation_id from another enrollment (-> 404).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.learner import Enrollment
from app.routers.tutor import get_tutor_ai
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import seed_version_with_members
from tests.tutor._helpers import FakeTutorAI, seed_enrollment_with_index


@asynccontextmanager
async def _transport(session: AsyncSession, tutor_ai=None):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    if tutor_ai is not None:
        app.dependency_overrides[get_tutor_ai] = lambda: tutor_ai
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str, sub: uuid.UUID | None = None, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(sub or uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_ask_requires_learner_role(db_session: AsyncSession):
    enrollment = await seed_enrollment_with_index(db_session, texts=["agentic ai"])
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "what is agentic ai"},
            headers=_auth("architect", sub=enrollment.learner_id),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ask_returns_grounded_answer_and_persists(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session,
        texts=["retrieval augmented generation grounds answers in course content"],
        learner_id=learner,
    )
    await db_session.commit()
    fake = FakeTutorAI(answer_text="RAG grounds answers in the course.")

    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "retrieval augmented generation grounds answers in course content"},
            headers=_auth("learner", sub=learner),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["answer"] == "RAG grounds answers in the course."
        assert body["citations"], "expected citations grounding the answer"
        assert body["citations"][0]["chunk_id"]
        cid = body["conversation_id"]

        # History round-trip: the full server-side record.
        hist = await client.get(
            f"/api/v1/learn/tutor/{enrollment.id}/conversations/{cid}",
            headers=_auth("learner", sub=learner),
        )
        assert hist.status_code == 200, hist.text
        h = hist.json()
        assert [m["role"] for m in h["messages"]] == ["learner", "tutor"]
        assert h["messages"][1]["citations"]


@pytest.mark.asyncio
async def test_ask_passes_session_language_to_tutor(db_session: AsyncSession):
    """T3b: the ``language`` on the ask body reaches the tutor seam; omitting it
    defaults to ``en``."""
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session,
        texts=["retrieval augmented generation grounds answers in course content"],
        learner_id=learner,
    )
    await db_session.commit()
    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        q = "retrieval augmented generation grounds answers in course content"
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": q, "language": "Spanish"},
            headers=_auth("learner", sub=learner),
        )
        assert resp.status_code == 200, resp.text
        assert fake.calls[-1].language == "Spanish"

        # Omitting language defaults to English.
        resp2 = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": q},
            headers=_auth("learner", sub=learner),
        )
        assert resp2.status_code == 200, resp2.text
        assert fake.calls[-1].language == "en"


@pytest.mark.asyncio
async def test_ask_empty_index_refuses(db_session: AsyncSession):
    # Released version with NO ingested chunks.
    learner = uuid.uuid4()
    version = await seed_version_with_members(db_session, texts=["unindexed"])
    enrollment = Enrollment(learner_id=learner, curriculum_version_id=version.id)
    db_session.add(enrollment)
    await db_session.flush()
    await db_session.commit()

    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "anything"},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "I don't have information about that in this course."
    assert body["citations"] == []
    assert fake.called is False


@pytest.mark.asyncio
async def test_ask_cross_learner_404(db_session: AsyncSession):
    owner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["agentic ai"], learner_id=owner
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "q"},
            headers=_auth("learner", sub=uuid.uuid4()),  # different learner
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ask_cross_tenant_404(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["agentic ai"], learner_id=learner
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        # Same learner sub, but a DIFFERENT tenant org claim → invisible → 404.
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "q"},
            headers=_auth("learner", sub=learner, org=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ask_conversation_from_other_enrollment_404(db_session: AsyncSession):
    learner = uuid.uuid4()
    e1 = await seed_enrollment_with_index(db_session, texts=["a"], learner_id=learner)
    e2 = await seed_enrollment_with_index(db_session, texts=["b"], learner_id=learner)
    await db_session.commit()
    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        # Start a conversation on e1.
        first = await client.post(
            f"/api/v1/learn/tutor/{e1.id}/ask",
            json={"question": "a"},
            headers=_auth("learner", sub=learner),
        )
        cid = first.json()["conversation_id"]
        # Reuse e1's conversation id under e2 → 404.
        resp = await client.post(
            f"/api/v1/learn/tutor/{e2.id}/ask",
            json={"question": "a", "conversation_id": cid},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_requires_learner_role(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=learner
    )
    await db_session.commit()
    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        first = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "a"},
            headers=_auth("learner", sub=learner),
        )
        cid = first.json()["conversation_id"]
        resp = await client.get(
            f"/api/v1/learn/tutor/{enrollment.id}/conversations/{cid}",
            headers=_auth("architect", sub=learner),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ask_503_without_api_key(db_session: AsyncSession):
    """With no override + no API key, get_tutor_ai must 503 before generation."""
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=learner
    )
    await db_session.commit()
    # No tutor_ai override → the real get_tutor_ai guard runs (no key in test env).
    async with _transport(db_session, tutor_ai=None) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/ask",
            json={"question": "a"},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 503
