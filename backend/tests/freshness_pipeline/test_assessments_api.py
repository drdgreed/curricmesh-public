"""Tests for the freshness-pipeline assessments API.

Uses the same _make_transport / _auth dependency-override transport pattern as
test_watchlist_api.py. No AI interaction — pure read-only CRUD.

Test sequence (ordered):
  GET empty → seed rows → GET returns them → ?recommendation= filter works
  → wrong role → 403
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.freshness_pipeline import GapAssessment
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_runner import _seed_curriculum


# ---------------------------------------------------------------------------
# Transport helpers (mirror test_watchlist_api.py)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_assessment(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    topic: str,
    recommendation: str,
    confidence: float = 0.8,
    promoted_ccr_id: uuid.UUID | None = None,
) -> GapAssessment:
    row = GapAssessment(
        curriculum_id=curriculum_id,
        topic=topic.lower(),
        display_topic=topic,
        recommendation=recommendation,
        confidence=confidence,
        scores={"evidence_strength": confidence},
        rationale="Test rationale.",
        dossier=[{"run_date": "2026-07-01", "source_kinds": ["corpus"], "evidence": ["test evidence"]}],
        times_seen=1,
        times_seen_at_last_eval=1,
        promoted_ccr_id=promoted_ccr_id,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_empty_assessments(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/freshness/assessments", headers=_auth("architect")
        )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_seeded_rows_round_trip(db_session: AsyncSession):
    cur, _ = await _seed_curriculum(db_session)
    await _seed_assessment(
        db_session,
        curriculum_id=cur.id,
        topic="Agentic AI",
        recommendation="monitor",
        confidence=0.65,
    )
    await _seed_assessment(
        db_session,
        curriculum_id=cur.id,
        topic="RAG Pipelines",
        recommendation="reject",
        confidence=0.3,
    )

    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/freshness/assessments", headers=_auth("program_manager")
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    topics = {r["topic"] for r in rows}
    assert topics == {"agentic ai", "rag pipelines"}
    # Verify key fields are present and correct
    monitor_row = next(r for r in rows if r["recommendation"] == "monitor")
    assert monitor_row["display_topic"] == "Agentic AI"
    assert monitor_row["confidence"] == pytest.approx(0.65)
    assert "scores" in monitor_row
    assert "dossier" in monitor_row
    assert "times_seen" in monitor_row
    assert monitor_row["promoted_ccr_id"] is None
    assert "first_seen_at" in monitor_row
    assert "last_evaluated_at" in monitor_row


@pytest.mark.asyncio
async def test_recommendation_filter(db_session: AsyncSession):
    cur, _ = await _seed_curriculum(db_session)
    await _seed_assessment(
        db_session, curriculum_id=cur.id, topic="Monitor Topic", recommendation="monitor"
    )
    await _seed_assessment(
        db_session, curriculum_id=cur.id, topic="Reject Topic", recommendation="reject"
    )
    await _seed_assessment(
        db_session, curriculum_id=cur.id, topic="Adopt Topic", recommendation="adopt_now"
    )

    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/freshness/assessments?recommendation=monitor",
            headers=_auth("architect"),
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["recommendation"] == "monitor"
    assert rows[0]["display_topic"] == "Monitor Topic"


@pytest.mark.asyncio
async def test_role_instructor_forbidden(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/freshness/assessments", headers=_auth("instructor")
        )
    assert resp.status_code == 403
