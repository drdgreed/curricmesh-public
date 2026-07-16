"""Phase 3 acceptance: pipeline end-to-end merge-through with generated content.

The full loop with all AI mocked:

  seed + backfill (active_content_version_id set explicitly so content cards
  and generation both engage)
    → run_org (FRESHNESS_GENERATION_ENABLED=True, scripted adopt@0.9 judge,
               fake enricher writing a modify_asset enrichment, fake generator
               returning MARKER content)
       → adopted CCR carries a valid ReleaseChangeSet
  → QA pass + 2 approvals (distinct users, ≥1 instructor) + merge()
    → new active CurriculumVersion (different id from the seeded one)
    → Curriculum.active_content_version_id updated
    → target member's ContentVersion.content contains MARKER
  → prior CurriculumVersion still exists (immutability)

Spec acceptance criterion #3 — the whole point of Phase 3.

Merge ritual mirrors tests/merge/test_merge.py: helpers reused directly
so any change to the engine gate propagates here automatically.
Engine-direct (not HTTP) is used for approvals and merge — same coverage,
avoids duplicating the HTTP test infrastructure.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register all models on Base.metadata)
from app.ai.schemas import GeneratedAssetContent
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.core.workflow.engine import record_approval, record_qa
from app.database import Base
from app.db.rls import apply_rls
from app.freshness_pipeline.runner import run_org
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import ContentVersion
from app.models.content_model import CurriculumVersion as ContentCV
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.freshness_pipeline import PipelineSeen
from app.models.org import Organization
from app.models.user import User
from app.models.workflow import ChangeRequest
from app.routers.approvals import merge
from app.schemas.release import ReleaseChangeSet
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

# Re-use pipeline fakes from test_runner (module-level functions, not fixtures).
from tests.freshness_pipeline.test_runner import (
    FakeAdoptAllJudge,
    FakeGapExtractor,
    _make_finding,
)

# ── Constants ────────────────────────────────────────────────────────────────

_ENUM_TYPES = ("lifecyclestatus", "assetkind")
_SEEDED_SLUG = "agentic-ai"

# Distinctive marker asserted end-to-end through the fork machinery.
MARKER = "# GENERATED CONTENT MARKER v2"

_QA_PASS = {
    "content_accuracy": 5,
    "alignment": 5,
    "prerequisites": 5,
    "consistency": 5,
    "instructor_support": 5,
    "student_experience": 5,
}


# ── Fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def seeded_backfilled_engine():
    """Fresh schema + full bootcamp seed + content-model backfill.

    Mirrors tests/merge/test_merge.py::seeded_backfilled_engine exactly so
    the merge ritual helpers work without modification.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    sfactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for ename in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {ename} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)

    async with sfactory() as session:
        await seed(session)
        await backfill_content_model(session)

    yield engine
    await engine.dispose()


# ── Session + domain helpers (mirror tests/merge/test_merge.py) ──────────────


async def _first_org(engine) -> uuid.UUID:
    sfactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sfactory() as s:
        return (await s.execute(select(Organization.id))).scalars().first()


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    """Open a raw AsyncSession with the org GUC set (caller must close)."""
    sfactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = sfactory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _get_curriculum(session: AsyncSession) -> Curriculum:
    cur = await session.scalar(
        select(Curriculum).where(Curriculum.slug == _SEEDED_SLUG)
    )
    assert cur is not None, f"seed missing {_SEEDED_SLUG!r} curriculum"
    return cur


async def _user_by_role(session: AsyncSession, role: str) -> User:
    u = await session.scalar(select(User).where(User.role == role))
    assert u is not None, f"seed missing user with role={role!r}"
    return u


async def _approve_gate(
    session: AsyncSession,
    ccr: ChangeRequest,
    instructor: User,
    second: User,
) -> None:
    """Satisfy can_release(): one passing QA + two distinct approvals (one instructor)."""
    await record_qa(
        session,
        ccr=ccr,
        reviewer_id=instructor.id,
        dimension_scores=_QA_PASS,
        verdict="pass",
    )
    await record_approval(
        session,
        ccr=ccr,
        approver_id=instructor.id,
        role="instructor",
        decision="approve",
    )
    await record_approval(
        session,
        ccr=ccr,
        approver_id=second.id,
        role="architect",
        decision="approve",
    )
    await session.flush()


# ── Fake generator ───────────────────────────────────────────────────────────


class _MarkerGenerator:
    """Returns distinctive MARKER content for every generate_asset_content call."""

    async def generate_asset_content(
        self,
        *,
        mode: str,
        current_content: str | None,
        draft_frame: dict,
        dossier: list[dict],
        style_samples: list[str],
        asset_kind: str,
        topic: str,
    ) -> GeneratedAssetContent:
        return GeneratedAssetContent(
            content=MARKER,
            summary_of_changes="Acceptance-test generated content.",
            caveats=[],
        )


# ── Acceptance test ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acceptance_pipeline_merge_through(
    seeded_backfilled_engine: Any, monkeypatch: Any
) -> None:
    """Phase 3 acceptance: pipeline → change_set → merge → generated content active.

    Drives the full Phase-3 loop end-to-end:

    1. Run the pipeline (generation ON, all AI mocked) against the seeded
       curriculum — the runner creates an adopted CCR with a valid
       ReleaseChangeSet whose changed[].content == MARKER.

    2. Drive the EXISTING merge path (engine-direct, same helpers as
       tests/merge/test_merge.py): QA pass + 2 approvals + merge().

    3. Assert a NEW active CurriculumVersion exists (different id from the
       original); its member for the target lineage_key resolves to a
       ContentVersion whose .content contains MARKER.

    4. Assert the prior CurriculumVersion still exists (immutability — nothing
       was destroyed).
    """
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            # ── Phase A: pre-run setup ────────────────────────────────────────
            # (a) Mark "not first run" so the runner processes signals rather than
            #     seeding the backlog silently.
            # (b) Activate the new-model pointer so build_content_cards and
            #     generate_change_set both engage (backfill leaves the pointer
            #     NULL; active_curriculum_version bridges via legacy semver).
            # (c) Discover the target lineage_key for the enricher closure.

            setup_session = await _open_org_session(engine, org_id)
            try:
                setup_session.add(PipelineSeen(signal_id="acceptance-prior-marker"))
                await setup_session.flush()

                cur = await _get_curriculum(setup_session)

                # Legacy-bridge path: active_content_version_id is NULL post-backfill;
                # active_curriculum_version uses the semver bridge to find the CV.
                cv = await active_curriculum_version(setup_session, cur.id)
                assert cv is not None, (
                    "backfill must produce a CurriculumVersion for the seeded curriculum"
                )
                old_cv_id = cv.id

                # Activate the new-model pointer so content cards + generation work.
                cur.active_content_version_id = cv.id
                setup_session.add(cur)
                await setup_session.flush()

                # Discover a lesson_plan lineage_key to target.
                members = await version_members(setup_session, cv.id)
                lp_members = [m for m in members if m.kind == AssetKind.lesson_plan]
                assert lp_members, (
                    "backfilled curriculum must have at least one lesson_plan member"
                )
                target_key = lp_members[0].lineage_key

                await setup_session.commit()
            finally:
                await setup_session.close()

            # ── Phase B: wire mocks ───────────────────────────────────────────

            async def _fake_fetch_all(*_, **__):
                from app.freshness_pipeline import PipelineSignal
                return [
                    PipelineSignal(
                        id="acceptance-sig-1",
                        source_kind="industry_news",
                        source="acceptance-test",
                        title="LLM Safety Frameworks",
                        url="https://example.com/acceptance-sig-1",
                        detail="Acceptance-test industry signal.",
                        captured_at="2026-07-05T00:00:00Z",
                    )
                ]

            async def _no_uni_signals(session, item, *, extractor, searcher, http, dry_run=False):
                return []

            # Enricher: writes a modify_asset enrichment targeting target_key.
            # The ccr_id FK is resolved via the session's identity map so the
            # runner's in-memory CCR object sees the updated impact.
            async def _enriching_enrich_ccr(session, *, ccr_id, enricher):
                ccr_obj = (
                    await session.execute(
                        select(ChangeRequest).where(ChangeRequest.id == ccr_id)
                    )
                ).scalar_one()
                new_impact = dict(ccr_obj.impact or {})
                new_impact["enrichment"] = {
                    "placement": {
                        "target_kind": "modify_asset",
                        "target_ref": target_key,
                        "position_hint": "replace existing lesson",
                        "rationale": "acceptance-test gap evidence",
                        "confidence": 0.9,
                    },
                    "draft_frame": {
                        "outline": ["Point A", "Point B"],
                        "sample_assessments": [],
                        "caveats": [],
                    },
                }
                ccr_obj.impact = new_impact
                session.add(ccr_obj)
                await session.flush()

            monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
            monkeypatch.setattr(
                "app.freshness_pipeline.university.check_watch_item", _no_uni_signals
            )
            monkeypatch.setattr(
                "app.freshness_pipeline.runner.enrich_ccr", _enriching_enrich_ccr
            )
            # Enable change-set generation (kill switch OFF by default).
            monkeypatch.setattr(
                "app.freshness_pipeline.runner.settings.FRESHNESS_GENERATION_ENABLED",
                True,
            )

            # ── Phase C: runner session factory ──────────────────────────────
            # The runner opens its own session(s) from this factory. Each session
            # carries the org GUC for RLS.  The runner commits internally; the
            # committed rows are visible to subsequent sessions on the same engine.

            @asynccontextmanager
            async def _runner_session():
                sfactory = async_sessionmaker(
                    engine, class_=AsyncSession, expire_on_commit=False
                )
                async with sfactory() as sess:
                    await sess.execute(
                        text("SELECT set_config('app.current_org', :org, false)"),
                        {"org": str(org_id)},
                    )
                    yield sess

            # ── Phase D: run the pipeline ─────────────────────────────────────
            # "patch" bump to bypass the mid-cohort guard (seeded curriculum has
            # an active cohort; create_gap_ccr uses instructor_override=False).

            run = await run_org(
                _runner_session,
                org_id,
                extractor=FakeGapExtractor([_make_finding("LLM Safety Frameworks", "patch")]),
                searcher=None,
                enricher=None,
                judge=FakeAdoptAllJudge(),
                generator=_MarkerGenerator(),
            )

            assert run.status == "ok", f"Pipeline run failed: {run.stats}"
            assert run.stats["ccrs_created"] == 1, (
                f"Expected 1 CCR, got {run.stats['ccrs_created']}"
            )
            assert run.stats["changesets_generated"] == 1, (
                f"Expected 1 change_set generated, got {run.stats['changesets_generated']}"
            )
            assert run.stats["changesets_failed"] == 0

            # ── Phase E: verify CCR carries a valid ReleaseChangeSet ──────────
            # Then drive the full merge ritual in the same session.

            merge_session = await _open_org_session(engine, org_id)
            try:
                # The seed already has demo CCRs; filter for the pipeline-created one.
                ccrs = (
                    await merge_session.execute(
                        select(ChangeRequest).where(
                            ChangeRequest.title == "[AI] LLM Safety Frameworks"
                        )
                    )
                ).scalars().all()
                assert len(ccrs) == 1, (
                    f"Expected exactly 1 AI pipeline CCR, got {len(ccrs)}: "
                    f"{[c.title for c in ccrs]}"
                )
                ccr = ccrs[0]

                assert ccr.change_set is not None, "CCR must carry a change_set"
                validated_cs = ReleaseChangeSet.model_validate(ccr.change_set)
                changed_keys = {c.lineage_key for c in validated_cs.changed}
                assert target_key in changed_keys, (
                    f"Expected {target_key!r} in change_set.changed; "
                    f"got {sorted(changed_keys)}"
                )

                # ── Phase F: merge ritual (mirrors test_merge.py) ─────────────
                # QA pass + 2 distinct approvals (one instructor) + engine-direct merge.

                instructor = await _user_by_role(merge_session, "instructor")
                second = await _user_by_role(merge_session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                await _approve_gate(merge_session, ccr, instructor, second)

                out = await merge(ccr.id, current=merger, db=merge_session)

                # ── Phase G: assert new active CurriculumVersion ──────────────

                assert out.status == "active", f"Merge result status: {out.status}"
                new_cv_id = out.version_id
                assert new_cv_id != old_cv_id, (
                    "Merge must produce a DIFFERENT CurriculumVersion id "
                    f"(old={old_cv_id}, new={new_cv_id})"
                )
                assert out.parent_version_id == old_cv_id, (
                    "New version's parent must be the pre-merge active version"
                )

                # Curriculum.active_content_version_id must be updated.
                cur_row = await _get_curriculum(merge_session)
                await merge_session.refresh(cur_row)
                assert cur_row.active_content_version_id == new_cv_id, (
                    "Curriculum.active_content_version_id must point to the new version"
                )

                # ── Phase H: generated content is in the new version ──────────

                new_members = await version_members(merge_session, new_cv_id)
                target_member = next(
                    (m for m in new_members if m.lineage_key == target_key), None
                )
                assert target_member is not None, (
                    f"New version must contain a member for lineage_key={target_key!r}"
                )

                cv_row = await merge_session.get(
                    ContentVersion, target_member.content_version_id
                )
                assert cv_row is not None, (
                    f"ContentVersion for member {target_key!r} not found"
                )
                assert MARKER in cv_row.content, (
                    f"Generated content must contain {MARKER!r}; "
                    f"got first 200 chars: {cv_row.content[:200]!r}"
                )

                # ── Phase I: immutability — old version still exists ──────────

                old_cv_row = await merge_session.get(ContentCV, old_cv_id)
                assert old_cv_row is not None, (
                    "Prior CurriculumVersion must still exist (nothing destroyed)"
                )

                # CCR moved to active (released) status.
                await merge_session.refresh(ccr)
                assert ccr.status.value == "active", (
                    f"CCR must be in active (released) status; got {ccr.status}"
                )

            finally:
                await merge_session.close()

    finally:
        current_org.reset(token)
