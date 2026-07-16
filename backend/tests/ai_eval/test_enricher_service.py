"""Tests for the Task-2 enrich_ccr service (placement + draft-frame into CCR impact).

ALL AI interaction is mocked via a ``FakeEnricher`` injected at the
``GapEnricher`` seam — ZERO real Anthropic calls / network in CI.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.enricher import enrich_ccr
from app.ai.schemas import CurriculumStructure, DraftFrame, GapFinding, Placement, SampleAssessment
from app.core.actors import ensure_ai_researcher
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import submit_ccr
from tests.ai_eval.test_gap_research import _seed_curriculum_with_version


class FakeEnricher:
    def __init__(self, placement: Placement, frame: DraftFrame):
        self._p, self._f = placement, frame
        self.seen_structure: CurriculumStructure | None = None

    async def place_gap(self, finding: GapFinding, structure: CurriculumStructure) -> Placement:
        self.seen_structure = structure
        return self._p

    async def draft_frame(self, finding: GapFinding, placement: Placement) -> DraftFrame:
        return self._f


async def _seed_ai_ccr(session: AsyncSession, cur):
    ai_user = await ensure_ai_researcher(session)
    ccr = await submit_ccr(
        session,
        curriculum_id=cur.id,
        author_id=ai_user.id,
        title="[AI] eval/observability",
        rationale="employers expect it",
        proposed_bump=BumpType.minor,
        affected_kinds=set(),
    )
    ccr.impact = {
        "ai_research": {
            "topic": "eval/observability",
            "coverage_status": "missing",
            "citations": ["job X"],
        }
    }
    session.add(ccr)
    await session.flush()
    return ccr


@pytest.mark.asyncio
async def test_enrich_writes_placement_and_draft_into_impact(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    ccr = await _seed_ai_ccr(db_session, cur)
    enricher = FakeEnricher(
        Placement(
            target_kind="new_module",
            target_ref=None,
            position_hint="after last module",
            rationale="new area",
            confidence=0.7,
        ),
        DraftFrame(
            outline=["intro", "hands-on"],
            sample_assessments=[SampleAssessment(stem="q", kind="mcq", answer_or_rubric="a")],
            caveats=["verify tooling"],
        ),
    )
    out = await enrich_ccr(db_session, ccr_id=ccr.id, enricher=enricher)
    enrich = out.impact["enrichment"]
    assert enrich["placement"]["target_kind"] == "new_module"
    assert enrich["draft_frame"]["outline"] == ["intro", "hands-on"]
    assert enrich["placement"]["confidence"] == 0.7
    # The finding was reconstructed from impact.ai_research and passed to the enricher.
    # The structure projection was built and handed in.
    assert enricher.seen_structure is not None
    # Enrichment is ADDITIVE — the prior ai_research key must survive (not clobbered).
    assert "ai_research" in out.impact


@pytest.mark.asyncio
async def test_enrich_rejects_invented_module_ref(db_session: AsyncSession):
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
    with pytest.raises(ValueError):
        await enrich_ccr(db_session, ccr_id=ccr.id, enricher=enricher)


@pytest.mark.asyncio
async def test_enrich_rejects_invented_asset_ref(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    ccr = await _seed_ai_ccr(db_session, cur)
    enricher = FakeEnricher(
        Placement(
            target_kind="modify_asset",
            target_ref="does-not-exist",  # not a real asset key
            position_hint="some asset",
            rationale="x",
            confidence=0.5,
        ),
        DraftFrame(outline=["a"]),
    )
    with pytest.raises(ValueError):
        await enrich_ccr(db_session, ccr_id=ccr.id, enricher=enricher)
