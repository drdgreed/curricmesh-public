"""API tests for the coach (B4) + assess (B5) tutor endpoints.

Transport mirrors test_tutor_api.py: an ASGITransport AsyncClient with get_db +
get_tutor_ai overridden (a FAKE tutor -> ZERO real Anthropic calls in CI), JWTs
minted via create_access_token carrying the org claim.

Covers, per endpoint: role gating (learner vs non-learner -> 403), happy path,
cross-learner + cross-tenant isolation (-> 404), and the 503-without-key guard.
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
from app.routers.tutor import get_tutor_ai
from tests.conftest import DEFAULT_ORG_ID
from tests.tutor._helpers import (
    FakeTutorAI,
    seed_assessment_submission,
    seed_enrollment_with_index,
)


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


# ---------------------------------------------------------------------------
# B4 — coach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coach_happy_path(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["intro", "grounding"], learner_id=learner
    )
    await db_session.commit()
    fake = FakeTutorAI(coach_text="Try the grounding lesson next.")
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"] == "Try the grounding lesson next."
    assert body["conversation_id"]
    assert body["citations"], "expected grounding citations"


@pytest.mark.asyncio
async def test_coach_passes_session_language(db_session: AsyncSession):
    """T3b: the ``language`` on the coach body reaches the coaching seam."""
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["intro", "grounding"], learner_id=learner
    )
    await db_session.commit()
    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={"language": "German"},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    assert fake.coach_calls[-1].language == "German"


@pytest.mark.asyncio
async def test_coach_requires_learner_role(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=learner
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={},
            headers=_auth("architect", sub=learner),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_coach_cross_learner_404(db_session: AsyncSession):
    owner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=owner
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={},
            headers=_auth("learner", sub=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_coach_cross_tenant_404(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=learner
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={},
            headers=_auth("learner", sub=learner, org=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_coach_503_without_api_key(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["a"], learner_id=learner
    )
    await db_session.commit()
    async with _transport(db_session, tutor_ai=None) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/coach",
            json={},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# B5 — assess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assess_happy_path_writes_back(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["course"], learner_id=learner
    )
    submission = await seed_assessment_submission(
        db_session,
        enrollment=enrollment,
        response_text="My grounded answer.",
        rubric="Criterion: is grounded.",
    )
    await db_session.commit()
    fake = FakeTutorAI(assess_score=0.8, assess_feedback="Well grounded.")
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["score"] == 0.8
    assert body["feedback"] == "Well grounded."
    assert body["submission_id"] == str(submission.id)

    # Persisted write-back.
    await db_session.refresh(submission)
    assert submission.score == 0.8
    assert submission.feedback == "Well grounded."


@pytest.mark.asyncio
async def test_assess_passes_session_language(db_session: AsyncSession):
    """T3b: the ``language`` query param reaches the assess seam (feedback only)."""
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["course"], learner_id=learner
    )
    submission = await seed_assessment_submission(
        db_session, enrollment=enrollment, response_text="My answer.", rubric="Crit."
    )
    await db_session.commit()
    fake = FakeTutorAI()
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            params={"language": "German"},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    assert fake.assess_calls[-1].language == "German"


@pytest.mark.asyncio
async def test_assess_requires_learner_role(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["c"], learner_id=learner
    )
    submission = await seed_assessment_submission(
        db_session, enrollment=enrollment, response_text="x", rubric="r"
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            headers=_auth("architect", sub=learner),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_assess_cross_learner_404(db_session: AsyncSession):
    owner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["c"], learner_id=owner
    )
    submission = await seed_assessment_submission(
        db_session, enrollment=enrollment, response_text="x", rubric="r"
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            headers=_auth("learner", sub=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assess_cross_tenant_404(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["c"], learner_id=learner
    )
    submission = await seed_assessment_submission(
        db_session, enrollment=enrollment, response_text="x", rubric="r"
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            headers=_auth("learner", sub=learner, org=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assess_submission_from_other_enrollment_404(db_session: AsyncSession):
    learner = uuid.uuid4()
    e1 = await seed_enrollment_with_index(db_session, texts=["a"], learner_id=learner)
    e2 = await seed_enrollment_with_index(db_session, texts=["b"], learner_id=learner)
    sub1 = await seed_assessment_submission(
        db_session, enrollment=e1, response_text="x", rubric="r"
    )
    await db_session.commit()
    async with _transport(db_session, FakeTutorAI()) as client:
        # sub1 belongs to e1; using it under e2 → 404.
        resp = await client.post(
            f"/api/v1/learn/tutor/{e2.id}/submissions/{sub1.id}/assess",
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assess_503_without_api_key(db_session: AsyncSession):
    learner = uuid.uuid4()
    enrollment = await seed_enrollment_with_index(
        db_session, texts=["c"], learner_id=learner
    )
    submission = await seed_assessment_submission(
        db_session, enrollment=enrollment, response_text="x", rubric="r"
    )
    await db_session.commit()
    async with _transport(db_session, tutor_ai=None) as client:
        resp = await client.post(
            f"/api/v1/learn/tutor/{enrollment.id}/submissions/{submission.id}/assess",
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 503
