"""Tests for Milestone B — AI CCR-impact guidance.

ALL AI interaction is mocked via a ``FakeAnalyzer`` injected at the
``get_impact_analyzer`` dependency — ZERO real Anthropic calls / network in CI.
No ``AIClient`` is ever constructed without being overridden away.

Coverage:
  * happy path (stateless preview) — POST returns the canned report, no DB write.
  * persistence — with ``ccr_id``, the report is written to the CCR's impact JSONB.
  * 503 — the real ``get_impact_analyzer`` refuses when no API key is configured.
  * 404 — unknown curriculum.
  * model round-trip — ``ImpactReport`` serializes + re-parses unchanged.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.ai.impact import ImpactAnalyzer, score_impact
from app.ai.schemas import ImpactReport
from app.auth.jwt import create_access_token
from app.config import settings
from app.database import Base, get_db
from app.db.rls import apply_rls
from app.main import app
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.org import Organization
from app.models.workflow import ChangeRequest
from app.routers.impact import get_impact_analyzer
from app.schemas.release import NewAssetIn, ReleaseChangeSet
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

_ENUM_TYPES = ("lifecyclestatus", "assetkind")
SEEDED_SLUG = "agentic-ai"


# ---------------------------------------------------------------------------
# Fake analyzer (the seam) — records inputs, returns a canned report.
# ---------------------------------------------------------------------------


def _canned_report() -> ImpactReport:
    return ImpactReport(
        summary="Adds one bonus lab; modest extra time, slightly higher load.",
        learning_objectives_impact="Reinforces the capstone synthesis objective.",
        affected_objectives=["Synthesize an end-to-end agentic system"],
        duration_delta_minutes=90,
        duration_rationale="One new ~90-minute lab.",
        cognitive_load="higher",
        cognitive_load_rationale="A new integrative lab raises synthesis demand.",
        risks=["Week 99 may become overloaded."],
        recommendations=["Consider making the lab optional."],
    )


class FakeAnalyzer:
    def __init__(self, report: ImpactReport) -> None:
        self._report = report
        self.seen_change_set: ReleaseChangeSet | None = None
        self.seen_title: str | None = None
        self.seen_context: str | None = None
        self.call_count = 0

    async def analyze_impact(
        self,
        *,
        change_set: ReleaseChangeSet,
        title: str | None = None,
        rationale: str | None = None,
        context: str | None = None,
    ) -> ImpactReport:
        self.call_count += 1
        self.seen_change_set = change_set
        self.seen_title = title
        self.seen_context = context
        return self._report


# ---------------------------------------------------------------------------
# Seeded + back-filled engine (manifest live) — same pattern as release tests.
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_backfilled_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)

    async with session_factory() as session:
        await seed(session)
        await backfill_content_model(session)

    yield engine
    await engine.dispose()


async def _first_org(engine) -> uuid.UUID:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(Organization.id))).scalars().first()


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _curriculum(session: AsyncSession) -> Curriculum:
    cur = await session.scalar(
        select(Curriculum).where(Curriculum.slug == SEEDED_SLUG)
    )
    assert cur is not None, "seed missing the agentic-ai curriculum"
    return cur


@asynccontextmanager
async def _transport(session: AsyncSession, analyzer: ImpactAnalyzer | None, org_id):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    if analyzer is not None:
        app.dependency_overrides[get_impact_analyzer] = lambda: analyzer
    try:
        with use_org(org_id):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str, org_id: uuid.UUID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=str(org_id))
    return {"Authorization": f"Bearer {token}"}


def _change_set() -> dict:
    return ReleaseChangeSet(
        bump="minor",
        added=[
            NewAssetIn(
                lineage_key="agentic-ai/v1/99/bonus_lab",
                kind=AssetKind.lab,
                content="# Bonus capstone lab",
                section="Week 99: Bonus",
                week_index=99,
                order=0,
            )
        ],
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Unit: ImpactReport round-trips.
# ---------------------------------------------------------------------------


def test_impact_report_round_trips():
    report = _canned_report()
    dumped = report.model_dump(mode="json")
    again = ImpactReport.model_validate(dumped)
    assert again == report
    # JSON-safe (the router persists model_dump(mode="json")).
    assert isinstance(dumped["duration_delta_minutes"], int)
    assert dumped["cognitive_load"] == "higher"


@pytest.mark.asyncio
async def test_score_impact_delegates_to_analyzer():
    analyzer = FakeAnalyzer(_canned_report())
    cs = ReleaseChangeSet(bump="patch")
    out = await score_impact(
        analyzer=analyzer, change_set=cs, title="T", rationale="R", context="CTX"
    )
    assert out == _canned_report()
    assert analyzer.call_count == 1
    assert analyzer.seen_title == "T"
    assert analyzer.seen_context == "CTX"


# ---------------------------------------------------------------------------
# Endpoint tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impact_preview_happy_path(seeded_backfilled_engine):
    """Stateless preview: 200, body matches the canned report, no DB write."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        session = await _open_org_session(engine, org_id)
        try:
            cur = await _curriculum(session)
            analyzer = FakeAnalyzer(_canned_report())
            async with _transport(session, analyzer, org_id) as client:
                resp = await client.post(
                    f"/api/v1/curricula/{cur.id}/impact",
                    json={"change_set": _change_set(), "title": "Add bonus lab"},
                    headers=_auth("instructor", org_id),
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body == _canned_report().model_dump(mode="json")
            assert analyzer.call_count == 1
            # The analyzer saw the change-set and the light curriculum context.
            assert analyzer.seen_title == "Add bonus lab"
            assert analyzer.seen_context is not None
        finally:
            await session.close()
    finally:
        current_org.reset(token)


@pytest.mark.asyncio
async def test_impact_persists_onto_ccr(seeded_backfilled_engine):
    """With ccr_id, the report is written to the CCR's impact JSONB and committed."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        session = await _open_org_session(engine, org_id)
        try:
            cur = await _curriculum(session)
            ccr = ChangeRequest(
                curriculum_id=cur.id,
                author_id=None,
                title="Add bonus lab",
                rationale="Reinforce capstone.",
                proposed_bump="minor",
                status=LifecycleStatus.draft,
            )
            session.add(ccr)
            await session.commit()
            ccr_id = ccr.id

            analyzer = FakeAnalyzer(_canned_report())
            async with _transport(session, analyzer, org_id) as client:
                resp = await client.post(
                    f"/api/v1/curricula/{cur.id}/impact",
                    json={"change_set": _change_set(), "ccr_id": str(ccr_id)},
                    headers=_auth("architect", org_id),
                )
            assert resp.status_code == 200, resp.text

            # Re-read the CCR on a fresh session to prove it committed.
            verify = await _open_org_session(engine, org_id)
            try:
                row = await verify.scalar(
                    select(ChangeRequest).where(ChangeRequest.id == ccr_id)
                )
                assert row.impact is not None
                assert row.impact == _canned_report().model_dump(mode="json")
            finally:
                await verify.close()
        finally:
            await session.close()
    finally:
        current_org.reset(token)


@pytest.mark.asyncio
async def test_impact_unknown_curriculum_404(seeded_backfilled_engine):
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        session = await _open_org_session(engine, org_id)
        try:
            analyzer = FakeAnalyzer(_canned_report())
            async with _transport(session, analyzer, org_id) as client:
                resp = await client.post(
                    f"/api/v1/curricula/{uuid.uuid4()}/impact",
                    json={"change_set": _change_set()},
                    headers=_auth("instructor", org_id),
                )
            assert resp.status_code == 404
            assert analyzer.call_count == 0
        finally:
            await session.close()
    finally:
        current_org.reset(token)


def test_get_impact_analyzer_503_when_no_api_key(monkeypatch):
    """No ANTHROPIC_API_KEY → the real dependency refuses with 503, never builds
    an AIClient. monkeypatch auto-restores the setting after."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    with pytest.raises(HTTPException) as exc:
        get_impact_analyzer()
    assert exc.value.status_code == 503
