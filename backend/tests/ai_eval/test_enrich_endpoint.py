"""Integration tests for POST /api/v1/ccrs/{ccr_id}/enrich.

ALL AI interaction is mocked via FakeEnricher injected at the get_ai_enricher
dependency seam — zero real Anthropic calls / network in CI.
"""
from __future__ import annotations

import pytest
import httpx
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db
from app.routers.enrich import get_ai_enricher
from app.ai.schemas import DraftFrame, Placement, SampleAssessment
from tests.ai_eval.test_enricher_service import FakeEnricher, _seed_ai_ccr
from tests.ai_eval.test_gap_research import _seed_curriculum_with_version, _auth


@asynccontextmanager
async def _transport(session: AsyncSession, enricher):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ai_enricher] = lambda: enricher
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_enrich_endpoint_populates_impact(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    ccr = await _seed_ai_ccr(db_session, cur)
    enricher = FakeEnricher(
        Placement(
            target_kind="new_module",
            target_ref=None,
            position_hint="end",
            rationale="x",
            confidence=0.6,
        ),
        DraftFrame(
            outline=["a"],
            sample_assessments=[SampleAssessment(stem="q", kind="mcq", answer_or_rubric="a")],
        ),
    )
    async with _transport(db_session, enricher) as client:
        resp = await client.post(f"/api/v1/ccrs/{ccr.id}/enrich", headers=_auth("architect"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["impact"]["enrichment"]["placement"]["target_kind"] == "new_module"
    assert body["impact"]["enrichment"]["draft_frame"]["outline"] == ["a"]


@pytest.mark.asyncio
async def test_enrich_endpoint_bad_ref_returns_400(db_session: AsyncSession):
    """ValueError from enrich_ccr (invented module ref) must surface as HTTP 400."""
    cur, _ = await _seed_curriculum_with_version(db_session)
    ccr = await _seed_ai_ccr(db_session, cur)
    enricher = FakeEnricher(
        Placement(
            target_kind="modify_module",
            target_ref="9999",  # not a real module index
            position_hint="Module 9999",
            rationale="x",
            confidence=0.5,
        ),
        DraftFrame(outline=["a"]),
    )
    async with _transport(db_session, enricher) as client:
        resp = await client.post(f"/api/v1/ccrs/{ccr.id}/enrich", headers=_auth("architect"))

    assert resp.status_code == 400
