"""Tests for app.freshness_pipeline.judging — the 4-case routing state machine.

FakeJudge returns scripted NetBenefitAssessments per call and records every
call (including the dossier it received) so tests can assert both the outcome
and the number / content of judge calls without touching the real API.

Test cases
----------
1. new topic + adopt_now@0.9 → CCR created, row adopt_now, promoted_ccr_id set, 1-sighting dossier.
2. new topic + adopt_now@0.3 (below threshold) → no CCR, row stored as monitor,
   scores["model_recommendation"] == "adopt_now".
3. new topic + reject → row reject; 2nd sighting resurrects (re-judge, times_seen_at_last_eval updated);
   3rd sighting skipped (judge NOT called — asserts call counts).
4. monitor re-appearing → dossier grows to 2 sightings, judge called with BOTH sightings;
   if new verdict is gated adopt → CCR created + promoted_ccr_id set.
5. already-promoted topic re-appearing → times_seen incremented, judge NOT called, no second CCR.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import GapFinding, NetBenefitAssessment
from app.core.actors import ensure_ai_researcher
from app.freshness_pipeline.judging import JudgeOutcome, route_finding
from app.models.freshness_pipeline import GapAssessment
from app.models.workflow import ChangeRequest
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_runner import _make_finding, _seed_curriculum


# ---------------------------------------------------------------------------
# FakeJudge
# ---------------------------------------------------------------------------


class FakeJudge:
    """Returns scripted NetBenefitAssessments in call order; records every call."""

    def __init__(self, responses: list[NetBenefitAssessment]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment:
        idx = len(self.calls)
        self.calls.append({"finding": finding, "covered_topics": covered_topics, "dossier": dossier})
        return self._responses[idx]

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assessment(
    recommendation: str = "monitor",
    confidence: float = 0.6,
) -> NetBenefitAssessment:
    return NetBenefitAssessment(
        evidence_strength=confidence,
        demand_signal=confidence,
        learner_value=confidence,
        curriculum_fit=confidence,
        effort_cost=0.8,
        urgency=confidence,
        competitive_signal=0.5,
        recommendation=recommendation,
        confidence=confidence,
        rationale=f"Test rationale — {recommendation} @ {confidence}.",
    )


# ---------------------------------------------------------------------------
# Test 1: new topic + adopt_now@0.9 → CCR created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_topic_adopt_creates_ccr(db_session: AsyncSession):
    """First sighting of a topic with a strong adopt_now verdict: CCR + assessment."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding = _make_finding("MCP Protocol", bump="patch")
    judge = FakeJudge([_make_assessment("adopt_now", 0.9)])

    outcome = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=["Python Fundamentals"],
        run_date="2026-07-05",
        author_id=ai_user.id,
    )

    assert outcome.action == "adopted"
    assert outcome.ccr is not None
    assert outcome.resurrected is False
    assert outcome.strengthened is False

    # Exactly 1 GapAssessment row.
    rows = (await db_session.execute(select(GapAssessment))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.topic == "mcp protocol"       # lowercase identity
    assert row.display_topic == "MCP Protocol"
    assert row.recommendation == "adopt_now"
    assert row.promoted_ccr_id == outcome.ccr.id
    assert len(row.dossier) == 1             # one sighting
    assert row.times_seen == 1
    assert row.times_seen_at_last_eval == 1

    # CCR carries the full assessment + dossier in impact.
    ccr = outcome.ccr
    assert "assessment" in ccr.impact
    assert "dossier" in ccr.impact
    assert ccr.impact["assessment"]["recommendation"] == "adopt_now"
    assert ccr.impact["dossier"] == row.dossier

    # Judge was called exactly once.
    assert judge.call_count == 1


# ---------------------------------------------------------------------------
# Test 2: new topic + adopt_now below threshold → monitored, model_recommendation preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_topic_below_threshold_monitored(db_session: AsyncSession):
    """adopt_now@0.3 is below the 0.5 threshold → stored as monitor, no CCR."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding = _make_finding("Agent Memory Patterns", bump="minor")
    judge = FakeJudge([_make_assessment("adopt_now", 0.3)])

    outcome = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-05",
        author_id=ai_user.id,
    )

    assert outcome.action == "monitored"
    assert outcome.ccr is None

    rows = (await db_session.execute(select(GapAssessment))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.recommendation == "monitor"
    assert row.promoted_ccr_id is None
    # Model's original verdict is preserved for audit.
    assert row.scores["model_recommendation"] == "adopt_now"

    # No CCR was created.
    ccr_count = (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one()
    assert ccr_count == 0

    assert judge.call_count == 1


# ---------------------------------------------------------------------------
# Test 3: reject → resurrection on doubled sightings; 3rd sighting skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_resurrection_and_skip(db_session: AsyncSession):
    """Reject row: resurrected on 2nd sighting; 3rd sighting skipped (judge not called).

    Asserts judge call counts explicitly:
      call 1 (first sighting) → 1 call
      call 2 (resurrection)   → 2 calls
      call 3 (below threshold) → still 2 calls (no new call)
    """
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding = _make_finding("Vendor Promo Noise", bump="patch")

    # Three scripted verdicts: reject, then reject again (after resurrection), then
    # a 3rd call that must NOT happen (would raise IndexError if called).
    judge = FakeJudge([
        _make_assessment("reject", 0.8),   # call 1: first sighting
        _make_assessment("reject", 0.7),   # call 2: resurrection on 2nd sighting
    ])

    # ── Sighting 1: new topic, verdict = reject ──────────────────────────────
    out1 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-05",
        author_id=ai_user.id,
    )
    assert out1.action == "rejected"
    assert out1.resurrected is False
    assert judge.call_count == 1

    row = (await db_session.execute(select(GapAssessment))).scalar_one()
    assert row.recommendation == "reject"
    assert row.times_seen == 1
    assert row.times_seen_at_last_eval == 1

    # ── Sighting 2: times_seen 1→2; 2 >= 2×1 → resurrection ────────────────
    out2 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-06",
        author_id=ai_user.id,
    )
    assert out2.action == "rejected"
    assert out2.resurrected is True
    assert judge.call_count == 2             # judge WAS called

    await db_session.refresh(row)
    assert row.times_seen == 2
    assert row.times_seen_at_last_eval == 2  # updated during resurrection
    assert "prev_confidence" in row.scores

    # ── Sighting 3: times_seen 2→3; 3 < 2×2=4 → skipped ────────────────────
    out3 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-07",
        author_id=ai_user.id,
    )
    assert out3.action == "skipped"
    assert judge.call_count == 2             # judge was NOT called for sighting 3

    await db_session.refresh(row)
    assert row.times_seen == 3
    assert row.times_seen_at_last_eval == 2  # NOT updated (no re-judge)


# ---------------------------------------------------------------------------
# Test 4: monitor re-appearing → accumulated dossier + potential promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_accumulates_and_promotes(db_session: AsyncSession):
    """Monitor topic: 2nd sighting passes BOTH sightings in dossier; gated adopt → CCR."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding1 = _make_finding("Tool-Use Orchestration", bump="patch")
    finding2 = GapFinding(
        topic="Tool-Use Orchestration",   # same topic, different evidence
        coverage_status="partial",
        evidence=["second source citation"],
        proposed_bump="patch",
        rationale="Tool-use orchestration is increasingly common.",
    )

    judge = FakeJudge([
        _make_assessment("monitor", 0.55),     # call 1: first sighting → monitor
        _make_assessment("adopt_now", 0.85),   # call 2: re-judge → promotion
    ])

    # ── Sighting 1: new → monitor ────────────────────────────────────────────
    out1 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding1,
        judge=judge,
        covered_topics=["Python"],
        run_date="2026-07-05",
        author_id=ai_user.id,
    )
    assert out1.action == "monitored"
    assert out1.ccr is None
    assert judge.call_count == 1

    row = (await db_session.execute(select(GapAssessment))).scalar_one()
    assert row.recommendation == "monitor"
    assert len(row.dossier) == 1

    # ── Sighting 2: re-judge with accumulated dossier ────────────────────────
    out2 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding2,
        judge=judge,
        covered_topics=["Python"],
        run_date="2026-07-06",
        author_id=ai_user.id,
    )
    assert out2.action == "adopted"
    assert out2.ccr is not None
    assert out2.strengthened is True         # 0.85 > 0.55
    assert judge.call_count == 2

    # The fake judge received BOTH sightings in the dossier on the 2nd call.
    call2_dossier = judge.calls[1]["dossier"]
    assert len(call2_dossier) == 2, "Judge must receive both sightings on 2nd call"

    await db_session.refresh(row)
    assert row.recommendation == "adopt_now"
    assert row.promoted_ccr_id == out2.ccr.id
    assert len(row.dossier) == 2
    assert "prev_confidence" in row.scores
    assert row.scores["prev_confidence"] == pytest.approx(0.55)

    # CCR carries assessment + dossier.
    ccr = out2.ccr
    assert ccr.impact["assessment"]["confidence"] == pytest.approx(0.85)
    assert len(ccr.impact["dossier"]) == 2


# ---------------------------------------------------------------------------
# Test 5: already-promoted topic → skip, no second CCR, judge not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promoted_topic_skipped(db_session: AsyncSession):
    """A topic that already has a CCR (promoted_ccr_id set) is skipped on re-appearance."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding = _make_finding("Agentic Evals", bump="patch")

    judge = FakeJudge([
        _make_assessment("adopt_now", 0.9),   # call 1: first sighting → promotion
        # A second call here would be a test failure — FakeJudge would raise IndexError.
    ])

    # ── Sighting 1: promote ──────────────────────────────────────────────────
    out1 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-05",
        author_id=ai_user.id,
    )
    assert out1.action == "adopted"
    assert out1.ccr is not None
    assert judge.call_count == 1

    # ── Sighting 2: already promoted → skip ──────────────────────────────────
    out2 = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=finding,
        judge=judge,
        covered_topics=[],
        run_date="2026-07-06",
        author_id=ai_user.id,
    )
    assert out2.action == "skipped"
    assert out2.ccr is None
    assert judge.call_count == 1             # judge NOT called
    # Defeat the identity map: prove the times_seen increment was FLUSHED (T4 review).
    await db_session.refresh(out2.assessment)
    assert out2.assessment.times_seen == 2

    # Exactly one CCR — no duplicate created.
    ccr_count = (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one()
    assert ccr_count == 1

    # times_seen incremented even on skip.
    row = (await db_session.execute(select(GapAssessment))).scalar_one()
    assert row.times_seen == 2


@pytest.mark.asyncio
async def test_adopt_gate_workflow_guard_demotes_to_monitor(db_session: AsyncSession, monkeypatch):
    """create_gap_ccr returning None (mid-cohort workflow guard) stores the gap as
    monitor — a guard-rejected bump is a timing problem, not a value judgment (T4 review)."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)

    async def _guarded_create(session, *, curriculum_id, finding, author_id):
        return None  # workflow guard rejected the bump

    monkeypatch.setattr("app.freshness_pipeline.judging.create_gap_ccr", _guarded_create)

    judge = FakeJudge([_make_assessment("adopt_now", 0.9)])
    out = await route_finding(
        db_session,
        curriculum_id=cur.id,
        finding=_make_finding("Mid Cohort Topic", bump="major"),
        judge=judge,
        covered_topics=["existing topic"],
        run_date="2026-07-06",
        author_id=ai_user.id,
    )
    assert out.action == "monitored"
    assert out.ccr is None
    await db_session.refresh(out.assessment)
    assert out.assessment.recommendation == "monitor"
    assert out.assessment.promoted_ccr_id is None


@pytest.mark.asyncio
async def test_orphaned_adopt_now_rejudged_not_duplicated(db_session: AsyncSession):
    """An adopt_now row whose CCR vanished (promoted_ccr_id SET NULL) is re-judged
    and UPDATED — never re-inserted (unique violation) (T5 review discovery)."""
    cur, _ = await _seed_curriculum(db_session)
    ai_user = await ensure_ai_researcher(db_session)
    finding = _make_finding("Orphan Topic", bump="patch")

    judge = FakeJudge([
        _make_assessment("adopt_now", 0.9),   # sighting 1 → promoted
        _make_assessment("monitor", 0.6),     # sighting 2 (orphaned) → re-judged
    ])

    out1 = await route_finding(
        db_session, curriculum_id=cur.id, finding=finding, judge=judge,
        covered_topics=[], run_date="2026-07-05", author_id=ai_user.id,
    )
    assert out1.action == "adopted" and out1.ccr is not None

    # Orphan the assessment: simulate the CCR's deletion (FK would SET NULL).
    out1.assessment.promoted_ccr_id = None
    db_session.add(out1.assessment)
    await db_session.flush()

    out2 = await route_finding(
        db_session, curriculum_id=cur.id, finding=finding, judge=judge,
        covered_topics=[], run_date="2026-07-06", author_id=ai_user.id,
    )
    assert judge.call_count == 2              # re-judged, not skipped
    assert out2.action == "monitored"
    # ONE row total — updated in place, no duplicate insert.
    rows = (await db_session.execute(select(GapAssessment))).scalars().all()
    assert len(rows) == 1
    assert rows[0].times_seen == 2
