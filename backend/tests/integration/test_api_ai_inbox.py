"""Integration tests for GET /api/v1/ai/inbox — Task C5.

Covers:
  - Happy path: AI-drafted CCR (with its impact.ai_research evidence) and an
    ai_draft QAReview (with ccr_title, dimension_scores, evidence) are surfaced.
  - A non-AI draft CCR is excluded from drafted_ccrs.
  - A verdict='pass' QAReview is excluded from draft_qa_reviews.
  - Empty DB (no AI user) → both lists empty, still 200.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.core.actors import ensure_ai_researcher
from app.database import get_db
from app.main import app
from tests.conftest import DEFAULT_ORG_ID
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.user import User
from app.models.workflow import ChangeRequest, QAReview


# ---------------------------------------------------------------------------
# Transport helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str = "instructor") -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_curriculum(session: AsyncSession) -> Curriculum:
    cur = Curriculum(name="Data Eng", slug=f"data-eng-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()
    return cur


async def _seed_ccr(
    session: AsyncSession,
    *,
    curriculum: Curriculum,
    author_id: uuid.UUID | None,
    status: LifecycleStatus = LifecycleStatus.draft,
    impact: dict | None = None,
    title: str = "AI CCR",
) -> ChangeRequest:
    ccr = ChangeRequest(
        curriculum_id=curriculum.id,
        author_id=author_id,
        title=title,
        rationale="because SOTA moved",
        proposed_bump="minor",
        impact=impact,
        status=status,
    )
    session.add(ccr)
    await session.flush()
    return ccr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_inbox_surfaces_ai_ccr_and_ai_draft_qa(db_session: AsyncSession):
    cur = await _seed_curriculum(db_session)
    ai = await ensure_ai_researcher(db_session)

    impact = {
        "ai_research": {
            "topic": "Vector databases",
            "coverage_status": "missing",
            "citations": ["https://arxiv.org/abs/1234.5678", "https://example.com/x"],
        }
    }
    ai_ccr = await _seed_ccr(
        db_session,
        curriculum=cur,
        author_id=ai.id,
        impact=impact,
        title="Add vector DB module",
    )

    qa = QAReview(
        ccr_id=ai_ccr.id,
        reviewer_id=None,
        dimension_scores={"clarity": 4, "rigor": 3},
        evidence={"clarity": "Objectives are explicit.", "rigor": "Lacks assessment."},
        verdict="ai_draft",
    )
    db_session.add(qa)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/ai/inbox", headers=_auth("qa_lead"))

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # AI-drafted CCR present, with its impact evidence
    ccrs = body["drafted_ccrs"]
    assert len(ccrs) == 1
    assert ccrs[0]["id"] == str(ai_ccr.id)
    assert ccrs[0]["title"] == "Add vector DB module"
    assert ccrs[0]["impact"]["ai_research"]["topic"] == "Vector databases"
    assert "https://arxiv.org/abs/1234.5678" in ccrs[0]["impact"]["ai_research"]["citations"]

    # AI-draft QA present, with title, scores, evidence
    reviews = body["draft_qa_reviews"]
    assert len(reviews) == 1
    assert reviews[0]["id"] == str(qa.id)
    assert reviews[0]["ccr_id"] == str(ai_ccr.id)
    assert reviews[0]["ccr_title"] == "Add vector DB module"
    assert reviews[0]["dimension_scores"] == {"clarity": 4, "rigor": 3}
    assert reviews[0]["evidence"]["rigor"] == "Lacks assessment."


async def test_inbox_excludes_non_ai_ccr_and_pass_qa(db_session: AsyncSession):
    cur = await _seed_curriculum(db_session)
    ai = await ensure_ai_researcher(db_session)

    # A human author, NOT the AI researcher
    human = User(email=f"human-{uuid.uuid4().hex[:8]}@x.io", display_name="Human", role="instructor")
    db_session.add(human)
    await db_session.flush()

    human_ccr = await _seed_ccr(
        db_session, curriculum=cur, author_id=human.id, title="Human CCR"
    )
    ai_ccr = await _seed_ccr(
        db_session, curriculum=cur, author_id=ai.id, title="AI CCR"
    )

    # A real (passed) human QA review — must NOT appear in the inbox
    passed_qa = QAReview(
        ccr_id=ai_ccr.id,
        reviewer_id=human.id,
        dimension_scores={"clarity": 5},
        verdict="pass",
    )
    db_session.add(passed_qa)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/ai/inbox", headers=_auth("architect"))

    assert resp.status_code == 200, resp.text
    body = resp.json()

    ccr_ids = {c["id"] for c in body["drafted_ccrs"]}
    assert str(ai_ccr.id) in ccr_ids
    assert str(human_ccr.id) not in ccr_ids

    assert body["draft_qa_reviews"] == []


async def test_inbox_forbidden_for_system_actor(db_session: AsyncSession):
    # The synthetic ``system`` actor (and any non-staff token) must be excluded.
    token = create_access_token(sub=str(uuid.uuid4()), role="system", org=DEFAULT_ORG_ID)
    headers = {"Authorization": f"Bearer {token}"}
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/ai/inbox", headers=headers)

    assert resp.status_code == 403, resp.text


async def test_inbox_empty_when_no_ai_user(db_session: AsyncSession):
    # No AI researcher seeded → drafted_ccrs empty; no ai_draft QA → empty.
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/ai/inbox", headers=_auth())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["drafted_ccrs"] == []
    assert body["draft_qa_reviews"] == []
