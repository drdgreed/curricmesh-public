"""Tests for the C3 AI-assisted QA judge (LLM-as-judge, 6 dimensions).

ALL AI interaction is mocked via a ``FakeJudge`` injected at the ``QAJudge``
seam — ZERO real Anthropic calls / network in CI. No ``AIClient`` is ever
constructed here without being overridden away.

The headline invariant under test: the AI judge writes a ``verdict='ai_draft'``
review that can NEVER satisfy the release gate (``can_release`` counts only
``verdict='pass'`` rows). A human QA Lead always makes the real call.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.qa_judge import (
    DimensionJudgement,
    QAJudge,
    QAJudgement,
    _build_proposed_changes,
    _format_change_set_bodies,
    _load_initial_release_bodies,
    score_qa,
)
from app.auth.jwt import create_access_token
from app.config import settings
from app.core.actors import ensure_ai_researcher
from app.core.workflow.engine import can_release, record_approval, record_qa
from app.core.workflow.rules import QA_DIMENSIONS
from app.database import get_db
from app.main import app
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.user import User
from app.models.version import Version
from app.models.workflow import ChangeRequest, QAReview
from app.routers.qa import get_ai_judge


# ---------------------------------------------------------------------------
# Fake judge (the seam) — records inputs, returns a canned judgement.
# ---------------------------------------------------------------------------


def _full_judgement(score: int = 4) -> QAJudgement:
    return QAJudgement(
        judgements=[
            DimensionJudgement(
                dimension=dim,
                score=score,
                evidence=f"Evidence for {dim}: looks solid.",
            )
            for dim in QA_DIMENSIONS
        ]
    )


class FakeJudge:
    def __init__(self, judgement: QAJudgement) -> None:
        self._judgement = judgement
        self.seen_summary: str | None = None
        self.seen_changes: str | None = None
        self.call_count = 0

    async def judge(self, ccr_summary: str, proposed_changes: str) -> QAJudgement:
        self.call_count += 1
        self.seen_summary = ccr_summary
        self.seen_changes = proposed_changes
        return self._judgement


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_ccr(
    session: AsyncSession,
    *,
    title: str = "Add MCP module",
    rationale: str = "MCP is in demand but absent from the curriculum.",
    change_set: dict | None = None,
) -> ChangeRequest:
    cur = Curriculum(name="AI Eng", slug=f"ai-eng-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    version = Version(
        curriculum_id=cur.id, major=1, minor=0, patch=0, status=LifecycleStatus.approved
    )
    session.add(version)
    await session.flush()

    ccr = ChangeRequest(
        curriculum_id=cur.id,
        author_id=None,
        title=title,
        rationale=rationale,
        proposed_bump="patch",
        status=LifecycleStatus.draft,
        target_version_id=version.id,
        change_set=change_set,
    )
    session.add(ccr)
    await session.flush()
    return ccr


async def _make_user(session: AsyncSession, *, role: str) -> User:
    user = User(
        email=f"{role}-{uuid.uuid4().hex[:8]}@curricmesh.test",
        display_name=role.title(),
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# score_qa unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_qa_creates_draft_review(db_session: AsyncSession):
    ccr = await _seed_ccr(db_session)
    judge = FakeJudge(_full_judgement(score=4))

    qa = await score_qa(db_session, ccr=ccr, judge=judge)

    ai_user = await ensure_ai_researcher(db_session)
    assert qa.verdict == "ai_draft"
    assert qa.reviewer_id == ai_user.id

    # Flat int scores for all six dimensions.
    assert set(qa.dimension_scores.keys()) == set(QA_DIMENSIONS)
    assert all(isinstance(v, int) for v in qa.dimension_scores.values())

    # Evidence strings for all six dimensions.
    assert set(qa.evidence.keys()) == set(QA_DIMENSIONS)
    assert all(isinstance(v, str) and v for v in qa.evidence.values())

    # Row was flushed (queryable) but NOT committed by score_qa.
    row = (
        await db_session.execute(select(QAReview).where(QAReview.id == qa.id))
    ).scalar_one()
    assert row.verdict == "ai_draft"
    assert db_session.in_transaction()  # an open, uncommitted transaction remains


@pytest.mark.asyncio
async def test_ai_draft_never_auto_passes_the_gate(db_session: AsyncSession):
    """The headline invariant: the AI draft can never satisfy the release gate."""
    ccr = await _seed_ccr(db_session)
    judge = FakeJudge(_full_judgement(score=5))

    await score_qa(db_session, ccr=ccr, judge=judge)

    # Two approvals including an instructor — gate's approval conditions are met.
    instructor = await _make_user(db_session, role="instructor")
    architect = await _make_user(db_session, role="architect")
    await record_approval(
        db_session, ccr=ccr, approver_id=instructor.id, role="instructor", decision="approve"
    )
    await record_approval(
        db_session, ccr=ccr, approver_id=architect.id, role="architect", decision="approve"
    )

    # Still cannot release: no HUMAN passing QA exists — the ai_draft is filtered out.
    assert await can_release(db_session, ccr) is False

    # A human QA Lead passes the review → gate now satisfied.
    qa_lead = await _make_user(db_session, role="qa_lead")
    await record_qa(
        db_session,
        ccr=ccr,
        reviewer_id=qa_lead.id,
        dimension_scores={dim: 5 for dim in QA_DIMENSIONS},
        verdict="pass",
    )
    assert await can_release(db_session, ccr) is True


@pytest.mark.asyncio
async def test_malformed_judgement_missing_dimension_raises(db_session: AsyncSession):
    ccr = await _seed_ccr(db_session)

    # A judgement covering only 5 of the 6 dimensions must fail validation.
    with pytest.raises(ValidationError):
        QAJudgement(
            judgements=[
                DimensionJudgement(dimension=dim, score=4, evidence="ok")
                for dim in QA_DIMENSIONS[:5]
            ]
        )

    # And no QAReview row was written for this CCR.
    rows = (
        await db_session.execute(select(QAReview).where(QAReview.ccr_id == ccr.id))
    ).scalars().all()
    assert rows == []


def test_malformed_judgement_extra_dimension_raises():
    """All six canonical dimensions PLUS an invented one must fail validation."""
    with pytest.raises(ValidationError):
        QAJudgement(
            judgements=[
                DimensionJudgement(dimension=dim, score=4, evidence="ok")
                for dim in QA_DIMENSIONS
            ]
            + [DimensionJudgement(dimension="invented_dim", score=4, evidence="ok")]
        )


def test_malformed_judgement_duplicate_dimension_raises():
    """A canonical dimension appearing twice must fail validation."""
    with pytest.raises(ValidationError):
        QAJudgement(
            judgements=[
                DimensionJudgement(dimension=dim, score=4, evidence="ok")
                for dim in QA_DIMENSIONS
            ]
            + [DimensionJudgement(dimension=QA_DIMENSIONS[0], score=4, evidence="dup")]
        )


@pytest.mark.asyncio
async def test_judge_receives_sensible_summary(db_session: AsyncSession):
    ccr = await _seed_ccr(
        db_session,
        title="Teach Async Python",
        rationale="Async is core to modern backends.",
    )
    judge = FakeJudge(_full_judgement())

    await score_qa(db_session, ccr=ccr, judge=judge)

    combined = (judge.seen_summary or "") + (judge.seen_changes or "")
    assert "Teach Async Python" in combined
    assert "Async is core to modern backends." in combined


@pytest.mark.asyncio
async def test_judge_sees_generated_asset_bodies(db_session: AsyncSession):
    """The core fix: the judge must see the ACTUAL generated content bodies
    (``change_set.added[].content`` / ``changed[].content``), not just the CCR
    title/rationale/impact. Without this the judge scored generated releases
    blind — it could not read what it was reviewing."""
    change_set = {
        "bump": {"major": 0, "minor": 1, "patch": 0},
        "added": [
            {
                "lineage_key": "lesson-mcp-intro",
                "kind": "lesson_plan",
                "content": (
                    "# MCP Intro\nThe Model Context Protocol lets agents call "
                    "tools over a standard wire format."
                ),
            }
        ],
        "changed": [
            {
                "lineage_key": "lesson-tools",
                "content": "Updated: tools now include get_weather().",
            }
        ],
        "removed": [],
    }
    ccr = await _seed_ccr(db_session, change_set=change_set)
    judge = FakeJudge(_full_judgement())

    await score_qa(db_session, ccr=ccr, judge=judge)

    seen = judge.seen_changes or ""
    # Actual body text of BOTH the new and the edited asset reaches the judge.
    assert "Model Context Protocol lets agents call" in seen
    assert "tools now include get_weather()" in seen
    # Each body is labelled so the judge knows which asset it is reading.
    assert "lesson-mcp-intro" in seen
    assert "lesson-tools" in seen


@pytest.mark.asyncio
async def test_judge_changes_without_change_set_is_unchanged(db_session: AsyncSession):
    """A description-only CCR (no ``change_set``) still scores — no crash — and
    emits no asset-body section (nothing to show)."""
    ccr = await _seed_ccr(db_session, rationale="Async is core.")
    judge = FakeJudge(_full_judgement())

    await score_qa(db_session, ccr=ccr, judge=judge)

    assert judge.call_count == 1
    assert "PROPOSED CONTENT" not in (judge.seen_changes or "")


@pytest.mark.asyncio
async def test_judge_body_content_is_bounded(db_session: AsyncSession):
    """A pathologically large body is truncated so an entire generated course
    can't blow the judge's context/cost — and the truncation is flagged so the
    model knows it saw a prefix, not the whole body."""
    huge = "X" * 200_000
    change_set = {
        "added": [
            {"lineage_key": "big-lesson", "kind": "lesson_plan", "content": huge}
        ],
        "changed": [],
        "removed": [],
    }
    ccr = await _seed_ccr(db_session, change_set=change_set)
    judge = FakeJudge(_full_judgement())

    await score_qa(db_session, ccr=ccr, judge=judge)

    seen = judge.seen_changes or ""
    assert "truncated" in seen.lower()  # truncation is visible to the model
    assert len(seen) < 100_000  # far smaller than the 200k raw body


# ---------------------------------------------------------------------------
# Authored initial-release path — content lives in ContentVersion rows under a
# candidate version (change_set is None). The judge must still see those bodies.
# ---------------------------------------------------------------------------


async def _seed_initial_release_ccr(
    session: AsyncSession, *, bodies: list[tuple[str, str]]
) -> ChangeRequest:
    """Seed an AUTHORED initial-release CCR the way the Course Builder publish
    path does: a candidate ``CurriculumVersion`` whose content lives in immutable
    ``ContentVersion`` rows, ``change_set=None``, and ``impact`` pointing at the
    candidate version. ``bodies`` is a list of ``(lineage_key, content)``."""
    cur = Curriculum(name="Authored", slug=f"authored-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    version = CurriculumVersion(curriculum_id=cur.id, major=1, minor=0, patch=0)
    session.add(version)
    await session.flush()

    for i, (key, content) in enumerate(bodies):
        lineage = LineageAsset(kind=AssetKind.lesson_plan, lineage_key=key)
        session.add(lineage)
        await session.flush()
        cv = ContentVersion(
            asset_id=lineage.id, seq=1, content=content, content_hash=f"h{i}"
        )
        session.add(cv)
        await session.flush()
        session.add(
            VersionMember(
                curriculum_version_id=version.id,
                asset_id=lineage.id,
                asset_version_id=cv.id,
                section="Week 0",
                week_index=0,
                order=i,
            )
        )
    await session.flush()

    ccr = ChangeRequest(
        curriculum_id=cur.id,
        author_id=None,
        title="[Initial Release] Authored Course",
        rationale="Initial release of an authored course.",
        proposed_bump="major",
        status=LifecycleStatus.draft,
        impact={"initial_release": {"candidate_version_id": str(version.id)}},
        change_set=None,
    )
    session.add(ccr)
    await session.flush()
    return ccr


@pytest.mark.asyncio
async def test_judge_sees_initial_release_bodies(db_session: AsyncSession):
    """The completion fix: an authored initial-release CCR keeps its content in
    ContentVersion rows (change_set is None). The judge must still see those
    bodies — loaded from the candidate version — not just the impact pointer."""
    ccr = await _seed_initial_release_ccr(
        db_session,
        bodies=[
            ("authored/v1/wk0/lesson_plan/aaa", "Agents plan, act, and observe in a loop."),
            ("authored/v1/wk0/lesson_plan/bbb", "Tool use lets an agent call get_weather()."),
        ],
    )
    judge = FakeJudge(_full_judgement())

    await score_qa(db_session, ccr=ccr, judge=judge)

    seen = judge.seen_changes or ""
    assert "Agents plan, act, and observe in a loop." in seen
    assert "Tool use lets an agent call get_weather()." in seen
    assert "authored/v1/wk0/lesson_plan/aaa" in seen
    assert "PROPOSED CONTENT" in seen


@pytest.mark.asyncio
async def test_load_initial_release_bodies_noop_for_plain_ccr(db_session: AsyncSession):
    """A non-initial-release CCR (no impact marker) yields no candidate-version
    bodies — score_qa appends nothing."""
    ccr = await _seed_ccr(db_session)
    assert await _load_initial_release_bodies(db_session, ccr) == ""


@pytest.mark.asyncio
async def test_load_initial_release_bodies_handles_malformed_pointer(
    db_session: AsyncSession,
):
    """A malformed candidate_version_id must not raise — QA scoring never crashes
    on a bad pointer; it degrades to no bodies."""
    ccr = await _seed_ccr(db_session)
    ccr.impact = {"initial_release": {"candidate_version_id": "not-a-uuid"}}
    await db_session.flush()
    assert await _load_initial_release_bodies(db_session, ccr) == ""


# ---------------------------------------------------------------------------
# Pure formatting unit tests (no DB) — the content-visibility logic in
# isolation, so it is provable without a Postgres/pgvector test database.
# ---------------------------------------------------------------------------


def test_format_bodies_includes_new_and_edited_content():
    change_set = {
        "added": [
            {"lineage_key": "a1", "kind": "lesson_plan", "content": "New body one."}
        ],
        "changed": [{"lineage_key": "a2", "content": "Edited body two."}],
    }
    out = _format_change_set_bodies(change_set)
    assert "New body one." in out
    assert "Edited body two." in out
    assert "NEW lesson_plan: a1" in out
    assert "EDITED asset: a2" in out


def test_format_bodies_empty_when_nothing_to_show():
    # None, non-dict, empty, and content-less items all yield no section.
    assert _format_change_set_bodies(None) == ""
    assert _format_change_set_bodies("not-a-dict") == ""  # type: ignore[arg-type]
    assert _format_change_set_bodies({"added": [], "changed": []}) == ""
    assert _format_change_set_bodies({"added": [{"lineage_key": "x"}]}) == ""


def test_format_bodies_is_defensive_against_malformed_items():
    # A non-dict item, a null-content item, and a missing lineage_key must not
    # raise — QA scoring can never crash on a bad change_set shape.
    change_set = {
        "added": ["oops-a-string", {"content": "kept body", "kind": "lab"}, None],
        "changed": [{"lineage_key": "c", "content": None}],
    }
    out = _format_change_set_bodies(change_set)
    assert "kept body" in out
    assert "(unknown)" in out  # missing lineage_key rendered safely


def test_format_bodies_caps_total_across_many_assets():
    # 40 assets * 6k chars would be 240k; the total cap keeps it bounded and
    # flags the omission.
    change_set = {
        "added": [
            {"lineage_key": f"k{i}", "kind": "lesson_plan", "content": "Y" * 6_000}
            for i in range(40)
        ],
        "changed": [],
    }
    out = _format_change_set_bodies(change_set)
    assert len(out) < 80_000
    assert "remaining asset bodies omitted" in out


def test_build_proposed_changes_combines_impact_and_bodies():
    ccr = ChangeRequest(
        curriculum_id=uuid.uuid4(),
        title="t",
        rationale="r",
        proposed_bump="minor",
        status=LifecycleStatus.draft,
        impact={"affected_asset_ids": ["a1"]},
        change_set={"added": [{"lineage_key": "a1", "kind": "lab", "content": "body!"}]},
    )
    out = _build_proposed_changes(ccr)
    assert "IMPACT ANALYSIS" in out
    assert "affected_asset_ids" in out
    assert "PROPOSED CONTENT" in out
    assert "body!" in out


def test_build_proposed_changes_falls_back_to_rationale():
    # No impact, no change_set → preserve the original rationale fallback.
    ccr = ChangeRequest(
        curriculum_id=uuid.uuid4(),
        title="t",
        rationale="just the rationale",
        proposed_bump="patch",
        status=LifecycleStatus.draft,
    )
    assert _build_proposed_changes(ccr) == "just the rationale"


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession, judge: QAJudge):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ai_judge] = lambda: judge
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
async def test_endpoint_qa_lead_creates_ai_draft(db_session: AsyncSession):
    ccr = await _seed_ccr(db_session)
    judge = FakeJudge(_full_judgement(score=3))

    async with _make_transport(db_session, judge) as client:
        resp = await client.post(
            f"/api/v1/ccrs/{ccr.id}/qa/ai-review", headers=_auth("qa_lead")
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["verdict"] == "ai_draft"
    assert set(body["dimension_scores"].keys()) == set(QA_DIMENSIONS)
    assert set(body["evidence"].keys()) == set(QA_DIMENSIONS)
    assert judge.call_count == 1


@pytest.mark.asyncio
async def test_endpoint_instructor_forbidden(db_session: AsyncSession):
    ccr = await _seed_ccr(db_session)
    judge = FakeJudge(_full_judgement())

    async with _make_transport(db_session, judge) as client:
        resp = await client.post(
            f"/api/v1/ccrs/{ccr.id}/qa/ai-review", headers=_auth("instructor")
        )

    assert resp.status_code == 403
    assert judge.call_count == 0


def test_get_ai_judge_503_when_no_api_key(monkeypatch):
    """With no ANTHROPIC_API_KEY, the dependency must refuse with 503 — never
    attempt a real AIClient. monkeypatch auto-restores the setting after."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    with pytest.raises(HTTPException) as exc:
        get_ai_judge()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_endpoint_missing_ccr_404(db_session: AsyncSession):
    judge = FakeJudge(_full_judgement())
    missing = uuid.uuid4()

    async with _make_transport(db_session, judge) as client:
        resp = await client.post(
            f"/api/v1/ccrs/{missing}/qa/ai-review", headers=_auth("qa_lead")
        )

    assert resp.status_code == 404
    assert judge.call_count == 0
