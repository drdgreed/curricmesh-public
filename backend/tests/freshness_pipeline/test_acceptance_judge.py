"""Acceptance integration test for the Phase-2 Judge — monitor → strengthen → promote.

Exercises the full RUNNER (run_org), not just route_finding, with all AI mocked.
Two runs walk the canonical Phase-2 promotion path:

  Run 1: one new signal → extractor yields "Agentic Observability" → judge scripted
         monitor@0.4 → no CCR; one gap_assessments row (monitor); gaps_monitored==1.

  Run 2: fresh signal id (passes seen-filter) → same topic finding → judge scripted
         adopt_now@0.8 → exactly ONE CCR created; CCR.impact carries assessment
         (confidence=0.8) + dossier (2 sightings) + ai_research; row is now
         adopt_now with promoted_ccr_id set; gaps_strengthened==1 (0.8 > 0.4);
         enrich_ccr attempted once.

Helpers/fakes are imported from test_runner (module-level), as directed by the plan.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import GapFinding, NetBenefitAssessment
from app.freshness_pipeline.runner import run_org
from app.models.freshness_pipeline import GapAssessment
from app.models.workflow import ChangeRequest
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_runner import (
    FakeGapExtractor,
    FakeRaisingGenerator,
    _industry_signal,
    _make_factory,
    _make_finding,
    _mark_not_first_run,
    _seed_curriculum,
    _seed_watch_item,
)


# ---------------------------------------------------------------------------
# Scripted judge: returns verdicts in call order; records every call + dossier.
# ---------------------------------------------------------------------------


def _make_nba(recommendation: str, confidence: float) -> NetBenefitAssessment:
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
        rationale=f"Scripted verdict: {recommendation} @ {confidence}.",
    )


class FakeScriptedJudge:
    """Returns scripted NetBenefitAssessments in call order; records each call.

    Shared across both runs of the journey so the second call reflects the
    second scripted verdict — mirrors FakeJudge in test_judging.py.
    """

    def __init__(self, responses: list[NetBenefitAssessment]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment:
        idx = len(self.calls)
        self.calls.append({"finding": finding, "dossier": dossier})
        return self._responses[idx]


# ---------------------------------------------------------------------------
# The acceptance journey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_to_strengthen_to_promote_journey(
    db_session: AsyncSession, monkeypatch
):
    """Full Phase-2 monitor → strengthen → promote journey through run_org.

    Spec acceptance criteria 1 + 2 (combined):
      1. Gated adopt_now → CCR with assessment + dossier in impact.
      2. Weak gap lands in monitor; subsequent run with more evidence re-judges
         (dossier grows; confidence delta computed); promotion creates exactly
         one CCR with the full dossier.
    """
    TOPIC = "Agentic Observability"

    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    # One judge instance shared across both runs; two scripted verdicts.
    judge = FakeScriptedJudge([
        _make_nba("monitor", 0.4),      # Run 1: first sighting → monitor
        _make_nba("adopt_now", 0.8),    # Run 2: re-judge with dossier → promote
    ])

    # Fake enrich_ccr — records the ccr_id; never touches impact (so impact
    # assertions below see only the fields written by the judging path).
    enrich_calls: list[uuid.UUID] = []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        enrich_calls.append(ccr_id)

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)
    monkeypatch.setattr(
        "app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item
    )

    # ── Run 1: one new signal, judge returns monitor@0.4 ────────────────────

    async def _fake_fetch_all_run1(*_, **__):
        return [_industry_signal(1)]

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all_run1)

    run1 = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=FakeGapExtractor([_make_finding(TOPIC)]),
        searcher=None,
        enricher=None,
        judge=judge,
        generator=FakeRaisingGenerator(),
    )

    assert run1.status == "ok"

    # No CCR for a monitored gap.
    assert run1.stats["ccrs_created"] == 0, "Run 1: no CCR for a monitored gap"
    assert run1.stats["gaps_adopted"] == 0

    # digest-visible stat: gaps_monitored == 1 (this key drives the JUDGE digest line).
    assert run1.stats["gaps_monitored"] == 1, "Run 1: gaps_monitored must be 1"
    assert run1.stats["gaps_judged"] == 1

    # No ChangeRequest row created.
    ccrs_after_run1 = (await db_session.execute(select(ChangeRequest))).scalars().all()
    assert len(ccrs_after_run1) == 0, "Run 1: no CCR in DB for a monitored gap"

    # Exactly one GapAssessment row, recommendation=monitor.
    assessments = (await db_session.execute(select(GapAssessment))).scalars().all()
    assert len(assessments) == 1, "Run 1: exactly one gap_assessments row"
    row = assessments[0]
    assert row.recommendation == "monitor"
    assert row.topic == TOPIC.lower(), "topic must be lowercase identity"
    assert row.confidence == pytest.approx(0.4)
    assert len(row.dossier) == 1, "Run 1: dossier has exactly 1 sighting"
    assert row.promoted_ccr_id is None

    # Judge called exactly once after Run 1.
    assert len(judge.calls) == 1

    # ── Run 2: fresh signal id (passes seen-filter), same topic, adopt_now@0.8 ──

    async def _fake_fetch_all_run2(*_, **__):
        # industry-2 is a fresh id — not in the seen ledger → new signal.
        return [_industry_signal(2)]

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all_run2)

    run2 = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=FakeGapExtractor([_make_finding(TOPIC)]),
        searcher=None,
        enricher=None,
        judge=judge,
        generator=FakeRaisingGenerator(),
    )

    assert run2.status == "ok"

    # Exactly one CCR promoted from the monitor queue.
    assert run2.stats["ccrs_created"] == 1, "Run 2: exactly one CCR promoted"
    assert run2.stats["gaps_adopted"] == 1
    assert run2.stats["gaps_judged"] == 1

    # Confidence rose 0.4 → 0.8: strengthened.
    assert run2.stats["gaps_strengthened"] == 1, "Run 2: 0.8 > 0.4 → gaps_strengthened==1"

    # Judge called twice total (once per run, no extra calls).
    assert len(judge.calls) == 2, "Judge must be called exactly once per run"
    # The second call received the accumulated dossier (2 sightings).
    assert len(judge.calls[1]["dossier"]) == 2, (
        "Run 2 judge call must receive the accumulated dossier (2 sightings)"
    )

    # Exactly ONE CCR in the database.
    ccrs = (await db_session.execute(select(ChangeRequest))).scalars().all()
    assert len(ccrs) == 1, "Exactly one CCR after both runs"
    ccr = ccrs[0]

    # CCR.impact carries all three expected keys.
    impact = ccr.impact
    assert "assessment" in impact, "CCR must carry assessment in impact"
    assert "dossier" in impact, "CCR must carry dossier in impact"
    assert "ai_research" in impact, "CCR must carry ai_research (from create_gap_ccr)"
    assert impact["assessment"]["confidence"] == pytest.approx(0.8)
    assert len(impact["dossier"]) == 2, "CCR dossier must have 2 sightings (one per run)"

    # GapAssessment row promoted: adopt_now with promoted_ccr_id set.
    await db_session.refresh(row)
    assert row.recommendation == "adopt_now"
    assert row.promoted_ccr_id == ccr.id
    assert row.confidence == pytest.approx(0.8)
    assert row.times_seen == 2
    assert len(row.dossier) == 2

    # Enrichment was attempted for the promoted CCR and only that CCR.
    assert len(enrich_calls) == 1, "enrich_ccr called exactly once for the promoted CCR"
    assert enrich_calls[0] == ccr.id, "enrich_ccr received the correct ccr_id"
