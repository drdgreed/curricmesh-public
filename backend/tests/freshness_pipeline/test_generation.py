"""Tests for ``app.freshness_pipeline.generation.generate_change_set``.

Seeding pattern mirrors ``test_content_cards.py`` (same immutable-model
helpers imported at module level).  A ``FakeContentGenerator`` returns canned
``GeneratedAssetContent`` with distinct, identifiable payloads per call so
assertions can target specific targets without caring about call ordering.

Test cases
----------
1. modify_asset_happy_path        — happy path: change_set.changed has full
                                    content; impact.generation written; validates
                                    as ReleaseChangeSet; changed[0].content ==
                                    the fake generator's content.
2. modify_module_lesson_plan      — modify_module resolves the lesson_plan
                                    member at the given week.
3. new_module_lesson_and_assess   — new_module produces lesson+assessment
                                    added entries with correct section/week/order;
                                    slug collision suffix applied.
4. lo_kind_target_returns_none    — modify_asset targeting learning_objectives →
                                    None + CCR change_set untouched.
5. missing_enrichment_returns_none — no enrichment in impact → None.
6. generator_raises_returns_none  — generator raising → None + CCR untouched.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import GeneratedAssetContent
from app.freshness_pipeline.generation import generate_change_set
from app.models.enums import AssetKind, LifecycleStatus
from app.models.workflow import ChangeRequest
from app.schemas.release import ReleaseChangeSet

# Re-use the immutable-model seeding helpers from test_content_cards (they are
# plain module-level async functions with no pytest fixtures mixed in).
from tests.freshness_pipeline.test_content_cards import (
    _make_content_version,
    _make_curriculum,
    _make_curriculum_version,
    _make_lineage,
    _make_member,
)


# ---------------------------------------------------------------------------
# Fake ContentGenerator
# ---------------------------------------------------------------------------


class FakeContentGenerator:
    """Returns canned GeneratedAssetContent; records every call."""

    CONTENT_TEMPLATE = "GENERATED CONTENT FOR {asset_kind} — mode={mode}"
    SUMMARY_TEMPLATE = "Summary for {asset_kind}."

    def __init__(self, raise_on_call: bool = False) -> None:
        self.calls: list[dict] = []
        self._raise = raise_on_call

    async def generate_asset_content(
        self, *, mode, current_content, draft_frame, dossier, style_samples, asset_kind, topic
    ) -> GeneratedAssetContent:
        if self._raise:
            raise RuntimeError("simulated generator failure")
        call = dict(
            mode=mode,
            current_content=current_content,
            draft_frame=draft_frame,
            dossier=dossier,
            style_samples=style_samples,
            asset_kind=asset_kind,
            topic=topic,
        )
        self.calls.append(call)
        return GeneratedAssetContent(
            content=self.CONTENT_TEMPLATE.format(asset_kind=asset_kind, mode=mode),
            summary_of_changes=self.SUMMARY_TEMPLATE.format(asset_kind=asset_kind),
            caveats=[f"verify {asset_kind} facts"],
        )


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


_ENRICHMENT_PLACEMENT_MODIFY_ASSET = lambda key: {
    "target_kind": "modify_asset",
    "target_ref": key,
    "position_hint": "replace existing lesson",
    "rationale": "gap evidence",
    "confidence": 0.9,
}

_ENRICHMENT_PLACEMENT_MODIFY_MODULE = lambda week: {
    "target_kind": "modify_module",
    "target_ref": str(week),
    "position_hint": f"week {week} module",
    "rationale": "gap evidence",
    "confidence": 0.9,
}

_ENRICHMENT_PLACEMENT_NEW_MODULE = {
    "target_kind": "new_module",
    "target_ref": None,
    "position_hint": "after last module",
    "rationale": "entirely new topic",
    "confidence": 0.85,
}

_ENRICHMENT_PLACEMENT_NEW_ASSET = lambda week: {
    "target_kind": "new_asset",
    "target_ref": str(week),
    "position_hint": f"into week {week}",
    "rationale": "supplement existing module",
    "confidence": 0.8,
}

_DRAFT_FRAME_NO_ASSESS = {
    "outline": ["Point A", "Point B"],
    "sample_assessments": [],
    "caveats": [],
}

_DRAFT_FRAME_WITH_ASSESS = {
    "outline": ["Point A", "Point B"],
    "sample_assessments": [
        {"stem": "What is X?", "kind": "short_answer", "answer_or_rubric": "X is Y."}
    ],
    "caveats": [],
}


def _ccr_impact(placement_dict: dict, draft_frame_dict: dict, topic: str = "MCP Integration") -> dict:
    return {
        "ai_research": {
            "topic": topic,
            "coverage_status": "missing",
            "citations": ["Source A"],
        },
        "dossier": [{"run_date": "2026-07-05", "evidence": ["Evidence 1"]}],
        "assessment": {"recommendation": "adopt_now", "confidence": 0.9},
        "enrichment": {
            "placement": placement_dict,
            "draft_frame": draft_frame_dict,
        },
    }


async def _make_ccr(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    impact: dict,
    proposed_bump: str = "minor",
    change_set: dict | None = None,
) -> ChangeRequest:
    ccr = ChangeRequest(
        curriculum_id=curriculum_id,
        title="[AI] MCP Integration",
        rationale="Gap detected",
        proposed_bump=proposed_bump,
        impact=impact,
        change_set=change_set,
        status=LifecycleStatus.draft,
    )
    session.add(ccr)
    await session.flush()
    return ccr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_asset_happy_path(db_session: AsyncSession):
    """Happy path: modify_asset generates ContentEditIn; validates as ReleaseChangeSet."""
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    asset = await _make_lineage(db_session, key="wk01/lesson_plan", kind=AssetKind.lesson_plan)
    body = "# Week 1\n\n## Core Concepts\nOriginal content here."
    content = await _make_content_version(db_session, asset=asset, seq=1, body=body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    impact = _ccr_impact(
        _ENRICHMENT_PLACEMENT_MODIFY_ASSET("wk01/lesson_plan"),
        _DRAFT_FRAME_NO_ASSESS,
    )
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact)

    generator = FakeContentGenerator()
    cs = await generate_change_set(db_session, ccr=ccr, generator=generator)

    # --- Return value ---
    assert cs is not None
    assert isinstance(cs, ReleaseChangeSet)

    # --- change_set written and validates ---
    assert ccr.change_set is not None
    validated = ReleaseChangeSet.model_validate(ccr.change_set)

    # --- Content matches fake generator ---
    assert len(validated.changed) == 1
    assert validated.changed[0].lineage_key == "wk01/lesson_plan"
    expected_content = FakeContentGenerator.CONTENT_TEMPLATE.format(
        asset_kind="lesson_plan", mode="edit"
    )
    assert validated.changed[0].content == expected_content

    # --- No additions ---
    assert validated.added == []

    # --- impact.generation written ---
    gen_meta = ccr.impact.get("generation")
    assert gen_meta is not None
    assert "wk01/lesson_plan" in gen_meta["targets"]
    assert "wk01/lesson_plan" in gen_meta["summaries"]
    assert gen_meta["generated_at"]  # non-empty ISO timestamp

    # --- Generator was called with correct kwargs ---
    assert len(generator.calls) == 1
    call = generator.calls[0]
    assert call["mode"] == "edit"
    assert call["current_content"] == body
    assert call["asset_kind"] == "lesson_plan"
    assert call["topic"] == "MCP Integration"

    # --- Bump ---
    assert validated.bump.value == "minor"


@pytest.mark.asyncio
async def test_modify_module_resolves_lesson_plan(db_session: AsyncSession):
    """modify_module: resolves lesson_plan member(s) at the given week_index."""
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    # Week 2 has one lesson_plan (the target) and one assessment (should be skipped).
    lp_asset = await _make_lineage(db_session, key="wk02/lesson_plan", kind=AssetKind.lesson_plan)
    lp_body = "# Week 2 Lesson\n\n## Intro\nContent."
    lp_content = await _make_content_version(db_session, asset=lp_asset, seq=1, body=lp_body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=lp_asset,
        content_version=lp_content,
        section="Week 2",
        week_index=2,
        order=0,
    )

    assess_asset = await _make_lineage(db_session, key="wk02/assessment", kind=AssetKind.assessment)
    assess_body = "Assessment content."
    assess_content = await _make_content_version(db_session, asset=assess_asset, seq=1, body=assess_body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=assess_asset,
        content_version=assess_content,
        section="Week 2",
        week_index=2,
        order=1,
    )

    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    impact = _ccr_impact(
        _ENRICHMENT_PLACEMENT_MODIFY_MODULE(2),
        _DRAFT_FRAME_NO_ASSESS,
    )
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact)

    generator = FakeContentGenerator()
    cs = await generate_change_set(db_session, ccr=ccr, generator=generator)

    assert cs is not None
    # Only lesson_plan at week 2 is targeted — not the assessment.
    assert len(cs.changed) == 1
    assert cs.changed[0].lineage_key == "wk02/lesson_plan"
    assert len(cs.added) == 0

    # Generator called once (lesson_plan only).
    assert len(generator.calls) == 1
    assert generator.calls[0]["mode"] == "edit"
    assert generator.calls[0]["current_content"] == lp_body


@pytest.mark.asyncio
async def test_new_module_lesson_and_assessment_with_slug_collision(db_session: AsyncSession):
    """new_module: lesson+assessment added at max_week+1; slug collision → -2 suffix."""
    topic = "MCP Integration"
    from app.freshness_pipeline.generation import _slugify
    base_slug = _slugify(topic)  # "mcp-integration"

    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    # Existing member occupying the base slug — forces -2 suffix.
    existing_asset = await _make_lineage(
        db_session, key=base_slug, kind=AssetKind.lesson_plan
    )
    existing_body = "# Existing lesson"
    existing_content = await _make_content_version(
        db_session, asset=existing_asset, seq=1, body=existing_body
    )
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=existing_asset,
        content_version=existing_content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    impact = _ccr_impact(
        _ENRICHMENT_PLACEMENT_NEW_MODULE,
        _DRAFT_FRAME_WITH_ASSESS,
        topic=topic,
    )
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact)

    generator = FakeContentGenerator()
    cs = await generate_change_set(db_session, ccr=ccr, generator=generator)

    assert cs is not None
    assert len(cs.changed) == 0
    assert len(cs.added) == 2

    lp = cs.added[0]
    assess = cs.added[1]

    # Slug collision: base slug taken → -2
    expected_lp_slug = f"{base_slug}-2"
    expected_assess_slug = f"{base_slug}-2-assessment"

    assert lp.lineage_key == expected_lp_slug
    assert lp.kind == AssetKind.lesson_plan
    assert lp.week_index == 2  # max_week (1) + 1
    assert lp.order == 0
    assert lp.section == topic

    assert assess.lineage_key == expected_assess_slug
    assert assess.kind == AssetKind.assessment
    assert assess.week_index == 2
    assert assess.order == 1
    assert assess.section == topic

    # Generator called twice (lesson_plan + assessment).
    assert len(generator.calls) == 2
    kinds_called = {c["asset_kind"] for c in generator.calls}
    assert kinds_called == {"lesson_plan", "assessment"}

    # Validate round-trip.
    validated = ReleaseChangeSet.model_validate(ccr.change_set)
    assert len(validated.added) == 2

    # impact.generation has both slugs in targets.
    gen_meta = ccr.impact["generation"]
    assert expected_lp_slug in gen_meta["targets"]
    assert expected_assess_slug in gen_meta["targets"]


@pytest.mark.asyncio
async def test_lo_kind_target_returns_none_untouched_ccr(db_session: AsyncSession):
    """modify_asset targeting learning_objectives → None; CCR unchanged."""
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    lo_asset = await _make_lineage(
        db_session, key="wk01/learning_objectives", kind=AssetKind.learning_objectives
    )
    lo_body = "# Learning Objectives\n- Understand X"
    lo_content = await _make_content_version(db_session, asset=lo_asset, seq=1, body=lo_body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=lo_asset,
        content_version=lo_content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    impact = _ccr_impact(
        _ENRICHMENT_PLACEMENT_MODIFY_ASSET("wk01/learning_objectives"),
        _DRAFT_FRAME_NO_ASSESS,
    )
    original_impact = dict(impact)
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact, change_set=None)

    generator = FakeContentGenerator()
    result = await generate_change_set(db_session, ccr=ccr, generator=generator)

    assert result is None
    # CCR untouched — change_set still None, generation key not in impact
    assert ccr.change_set is None
    assert "generation" not in (ccr.impact or {})
    # Generator never called
    assert generator.calls == []


@pytest.mark.asyncio
async def test_missing_enrichment_returns_none(db_session: AsyncSession):
    """No enrichment in impact → None; no DB changes."""
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    asset = await _make_lineage(db_session, key="wk01/lesson_plan", kind=AssetKind.lesson_plan)
    body = "Some content."
    content = await _make_content_version(db_session, asset=asset, seq=1, body=body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )
    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    # Impact without "enrichment" key.
    impact_no_enrichment = {
        "ai_research": {
            "topic": "MCP Integration",
            "coverage_status": "missing",
            "citations": [],
        },
        "dossier": [],
    }
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact_no_enrichment)

    generator = FakeContentGenerator()
    result = await generate_change_set(db_session, ccr=ccr, generator=generator)

    assert result is None
    assert ccr.change_set is None
    assert "generation" not in (ccr.impact or {})


@pytest.mark.asyncio
async def test_generator_raises_returns_none_ccr_untouched(db_session: AsyncSession):
    """Generator raising → None; CCR.change_set and impact unchanged."""
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    asset = await _make_lineage(db_session, key="wk01/lesson_plan", kind=AssetKind.lesson_plan)
    body = "# Week 1\nContent."
    content = await _make_content_version(db_session, asset=asset, seq=1, body=body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )
    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    sentinel_impact = _ccr_impact(
        _ENRICHMENT_PLACEMENT_MODIFY_ASSET("wk01/lesson_plan"),
        _DRAFT_FRAME_NO_ASSESS,
    )
    ccr = await _make_ccr(
        db_session,
        curriculum_id=cur.id,
        impact=sentinel_impact,
        change_set=None,
    )
    # Capture state before generation.
    original_change_set = ccr.change_set
    original_impact_keys = set((ccr.impact or {}).keys())

    raising_generator = FakeContentGenerator(raise_on_call=True)
    result = await generate_change_set(db_session, ccr=ccr, generator=raising_generator)

    assert result is None
    # change_set untouched.
    assert ccr.change_set == original_change_set
    # No "generation" key was added.
    assert "generation" not in (ccr.impact or {})
    # Original keys preserved.
    assert set((ccr.impact or {}).keys()) == original_impact_keys


@pytest.mark.asyncio
async def test_new_asset_into_target_module_and_fallback(db_session: AsyncSession):
    """new_asset lands in the target module's section/week; invalid target_ref
    falls back to max_week+1 (T4 review — branch was untested)."""
    topic = "Guardrail Patterns"
    cur = await _make_curriculum(db_session)
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    host_asset = await _make_lineage(db_session, key="host-lesson", kind=AssetKind.lesson_plan)
    host_content = await _make_content_version(db_session, asset=host_asset, seq=1, body="# Host")
    await _make_member(
        db_session, curriculum_version_id=cv_ver.id, asset=host_asset,
        content_version=host_content, section="Agent Safety", week_index=3, order=0,
    )
    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    # -- resolved module path: target_ref="3" → section/week of the host module
    impact = _ccr_impact(_ENRICHMENT_PLACEMENT_NEW_ASSET("3"), _DRAFT_FRAME_NO_ASSESS, topic=topic)
    ccr = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact)
    cs = await generate_change_set(db_session, ccr=ccr, generator=FakeContentGenerator())
    assert cs is not None and len(cs.added) == 1 and len(cs.changed) == 0
    new_asset = cs.added[0]
    assert new_asset.kind == AssetKind.lesson_plan
    assert new_asset.week_index == 3
    assert new_asset.section == "Agent Safety"

    # -- fallback path: non-numeric target_ref → max_week+1, section = topic
    impact2 = _ccr_impact(_ENRICHMENT_PLACEMENT_NEW_ASSET("not-a-week"), _DRAFT_FRAME_NO_ASSESS, topic=topic)
    ccr2 = await _make_ccr(db_session, curriculum_id=cur.id, impact=impact2)
    cs2 = await generate_change_set(db_session, ccr=ccr2, generator=FakeContentGenerator())
    assert cs2 is not None and len(cs2.added) == 1
    fallback_asset = cs2.added[0]
    assert fallback_asset.week_index == 4  # max_week (3) + 1
    assert fallback_asset.section == topic
