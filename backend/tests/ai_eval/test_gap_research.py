"""Tests for the C2 SOTA-gap researcher agent.

ALL AI interaction is mocked via a ``FakeExtractor`` injected at the
``GapExtractor`` seam — ZERO real Anthropic calls / network in CI. No ``AIClient``
is ever constructed here without being overridden away.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import GapExtractor
from app.ai.schemas import CorpusDoc, GapFinding
from app.ai.sota_researcher import analyze_gaps
from app.core.actors import ensure_ai_researcher
from app.auth.jwt import create_access_token
from app.core.workflow.engine import record_approval
from app.core.workflow.rules import WorkflowError
from app.database import get_db
from app.main import app
from app.models.cohort import Cohort
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.sota import SotaFinding, SotaSource
from app.models.structure import Module, Project
from app.models.version import Version
from app.models.workflow import ChangeRequest
from app.routers.research import get_ai_extractor


# ---------------------------------------------------------------------------
# Fake extractor (the seam) — records inputs, returns canned findings.
# ---------------------------------------------------------------------------


class FakeExtractor:
    def __init__(self, findings: list[GapFinding]) -> None:
        self._findings = findings
        self.seen_covered_topics: list[str] | None = None
        self.seen_corpus_docs: list[CorpusDoc] | None = None
        self.call_count = 0

    async def extract_gaps(
        self, covered_topics: list[str], corpus_docs: list[CorpusDoc]
    ) -> list[GapFinding]:
        self.call_count += 1
        self.seen_covered_topics = covered_topics
        self.seen_corpus_docs = corpus_docs
        return self._findings


def _two_findings() -> list[GapFinding]:
    return [
        GapFinding(
            topic="Model Context Protocol (MCP)",
            coverage_status="missing",
            evidence=["JD: 'experience with MCP servers'"],
            proposed_bump="patch",
            rationale="MCP is in demand but absent from the curriculum.",
        ),
        GapFinding(
            topic="Agent Observability",
            coverage_status="partial",
            evidence=["Vendor doc: tracing for agentic systems"],
            proposed_bump="minor",
            rationale="Observability for agents is under-covered.",
        ),
    ]


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_curriculum_with_version(
    session: AsyncSession,
    *,
    module_focuses: list[str] | None = None,
    project_titles: list[str] | None = None,
    set_current: bool = True,
) -> tuple[Curriculum, Version]:
    module_focuses = module_focuses or ["Python Fundamentals", "REST APIs"]
    project_titles = project_titles or ["Capstone: Build a SaaS"]

    cur = Curriculum(name="AI Eng", slug=f"ai-eng-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    version = Version(curriculum_id=cur.id, major=1, minor=0, patch=0, status=LifecycleStatus.active)
    session.add(version)
    await session.flush()

    for i, focus in enumerate(module_focuses):
        session.add(Module(version_id=version.id, index=i, focus=focus))
    for i, title in enumerate(project_titles):
        session.add(Project(version_id=version.id, index=i, title=title))
    await session.flush()

    if set_current:
        cur.current_version_id = version.id
        session.add(cur)
        await session.flush()

    return cur, version


async def _seed_corpus(session: AsyncSession, n: int = 2) -> list[SotaSource]:
    sources = []
    for i in range(n):
        s = SotaSource(
            title=f"Job Posting {i}",
            kind="job_posting",
            body="We need engineers fluent in MCP and agent observability.",
        )
        session.add(s)
        sources.append(s)
    await session.flush()
    return sources


async def _seed_active_cohort(session: AsyncSession, curriculum_id: uuid.UUID, version_id: uuid.UUID) -> Cohort:
    today = date.today()
    cohort = Cohort(
        curriculum_id=curriculum_id,
        version_id=version_id,
        name="Spring Cohort",
        start_date=today - timedelta(days=7),
        end_date=today + timedelta(days=30),
    )
    session.add(cohort)
    await session.flush()
    return cohort


# ---------------------------------------------------------------------------
# analyze_gaps unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_gaps_creates_draft_ccrs(db_session: AsyncSession):
    cur, version = await _seed_curriculum_with_version(db_session)
    await _seed_corpus(db_session, n=2)
    extractor = FakeExtractor(_two_findings())

    ccrs = await analyze_gaps(
        db_session, curriculum_id=cur.id, version=version, corpus=await _all_corpus(db_session), extractor=extractor
    )

    assert len(ccrs) == 2
    ai_user = await ensure_ai_researcher(db_session)
    bumps = {c.title: c.proposed_bump for c in ccrs}
    for c in ccrs:
        assert c.status == LifecycleStatus.draft
        assert c.author_id == ai_user.id
        assert c.impact["ai_research"]["citations"]
    assert bumps["[AI] Model Context Protocol (MCP)"] == "patch"
    assert bumps["[AI] Agent Observability"] == "minor"

    # Two SotaFinding evidence rows persisted.
    findings = (await db_session.execute(select(SotaFinding).where(SotaFinding.curriculum_id == cur.id))).scalars().all()
    assert len(findings) == 2
    topics = {f.topic for f in findings}
    assert topics == {"Model Context Protocol (MCP)", "Agent Observability"}


@pytest.mark.asyncio
async def test_ensure_ai_researcher_is_get_or_create(db_session: AsyncSession):
    u1 = await ensure_ai_researcher(db_session)
    u2 = await ensure_ai_researcher(db_session)
    assert u1.id == u2.id
    assert u1.role == "system"
    assert u1.display_name == "AI Researcher"


@pytest.mark.asyncio
async def test_extractor_receives_covered_topics(db_session: AsyncSession):
    cur, version = await _seed_curriculum_with_version(
        db_session, module_focuses=["Async Python", "GraphQL"], project_titles=["Final Project"]
    )
    await _seed_corpus(db_session)
    extractor = FakeExtractor(_two_findings())

    await analyze_gaps(
        db_session, curriculum_id=cur.id, version=version, corpus=await _all_corpus(db_session), extractor=extractor
    )

    assert extractor.seen_covered_topics is not None
    assert "Async Python" in extractor.seen_covered_topics
    assert "GraphQL" in extractor.seen_covered_topics
    assert "Final Project" in extractor.seen_covered_topics


@pytest.mark.asyncio
async def test_drafts_enter_normal_flow_no_bypass(db_session: AsyncSession):
    cur, version = await _seed_curriculum_with_version(db_session)
    await _seed_corpus(db_session)
    extractor = FakeExtractor(_two_findings())

    ccrs = await analyze_gaps(
        db_session, curriculum_id=cur.id, version=version, corpus=await _all_corpus(db_session), extractor=extractor
    )
    ccr = ccrs[0]
    ai_user = await ensure_ai_researcher(db_session)

    # Real, queryable draft row.
    row = (await db_session.execute(select(ChangeRequest).where(ChangeRequest.id == ccr.id))).scalar_one()
    assert row.status == LifecycleStatus.draft

    # The author (AI) cannot approve its own draft — the workflow guard holds.
    with pytest.raises(WorkflowError):
        await record_approval(
            db_session, ccr=ccr, approver_id=ai_user.id, role="program_manager", decision="approve"
        )


@pytest.mark.asyncio
async def test_mid_cohort_minor_skipped_batch_not_aborted(db_session: AsyncSession):
    cur, version = await _seed_curriculum_with_version(db_session)
    await _seed_corpus(db_session)
    await _seed_active_cohort(db_session, cur.id, version.id)

    findings = [
        GapFinding(
            topic="Blocked Minor",
            coverage_status="missing",
            evidence=["evidence"],
            proposed_bump="minor",
            rationale="should be blocked mid-cohort",
        ),
        GapFinding(
            topic="Allowed Patch",
            coverage_status="partial",
            evidence=["evidence"],
            proposed_bump="patch",
            rationale="patch allowed mid-cohort",
        ),
    ]
    extractor = FakeExtractor(findings)

    ccrs = await analyze_gaps(
        db_session, curriculum_id=cur.id, version=version, corpus=await _all_corpus(db_session), extractor=extractor
    )

    assert len(ccrs) == 1
    assert ccrs[0].title == "[AI] Allowed Patch"
    assert ccrs[0].proposed_bump == "patch"


async def _all_corpus(session: AsyncSession) -> list[SotaSource]:
    return list((await session.execute(select(SotaSource))).scalars().all())


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession, extractor: GapExtractor):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ai_extractor] = lambda: extractor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str) -> dict:
    from tests.conftest import DEFAULT_ORG_ID

    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_endpoint_architect_creates_drafts(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    await _seed_corpus(db_session)
    extractor = FakeExtractor(_two_findings())

    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(f"/api/v1/curricula/{cur.id}/research", headers=_auth("architect"))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body) == 2
    assert all(c["status"] == "draft" for c in body)
    assert extractor.call_count == 1

    # CCRs must be authored by the AI Researcher system actor, NOT the human
    # caller — guards against the router wrongly authoring as the JWT subject.
    ai_user = await ensure_ai_researcher(db_session)
    assert all(c["author_id"] == str(ai_user.id) for c in body)


@pytest.mark.asyncio
async def test_endpoint_instructor_forbidden(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    await _seed_corpus(db_session)
    extractor = FakeExtractor(_two_findings())

    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(f"/api/v1/curricula/{cur.id}/research", headers=_auth("instructor"))

    assert resp.status_code == 403
    assert extractor.call_count == 0


@pytest.mark.asyncio
async def test_endpoint_missing_curriculum_404(db_session: AsyncSession):
    extractor = FakeExtractor(_two_findings())
    missing = uuid.uuid4()

    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(f"/api/v1/curricula/{missing}/research", headers=_auth("architect"))

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_empty_corpus_400(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    extractor = FakeExtractor(_two_findings())

    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(f"/api/v1/curricula/{cur.id}/research", headers=_auth("architect"))

    assert resp.status_code == 400
    assert "corpus" in resp.json()["detail"].lower()
    assert extractor.call_count == 0
