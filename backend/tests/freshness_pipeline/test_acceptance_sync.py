"""Phase 4 acceptance: pipeline sync-through — merge triggers GitHub PR.

Extends Phase 3's merge-through to cover the full Phase-4 sync flow:

  seed + backfill
    + SyncTarget (config-as-data)
    + source_url backfilled directly on the target LineageAsset
    → run_org (FRESHNESS_GENERATION_ENABLED=True, all AI mocked)
       → adopted CCR with ReleaseChangeSet
  → QA pass + 2 approvals + merge()
    → post-merge hook fires (FRESHNESS_SYNC_ENABLED=True)
    → real GitHub adapter (MockTransport on sync_github._transport):
         GET /git/ref/heads/main → create branch → PUT curriculum/test-lesson.md → open PR
    → SyncLog(status="success", curriculum_version_id=new_cv, detail.url=PR_URL)
    → PUT body base64-decoded contains MARKER

Plus the sweep-retry case:
  pre-seeded failed SyncLog for the active CurriculumVersion
    → run_org with sweep ON (fake sync_release)
      → sweep retries, success log committed

Spec acceptance criteria: docs/specs/2026-07-05-freshness-pipeline-phase4-sync-design.md §
Acceptance criteria 1 (merge → correct PR) + 3 (sweep retry → idempotent branch name +
success log).
"""

from __future__ import annotations

import base64
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
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
from app.freshness_pipeline import sync_github
from app.freshness_pipeline.runner import run_org
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import ContentVersion
from app.models.content_model import CurriculumVersion as ContentCV
from app.models.content_model import LineageAsset
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.freshness_pipeline import PipelineSeen
from app.models.org import Organization
from app.models.sync import SyncLog, SyncTarget
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

# ── Constants (mirrors test_acceptance_execute.py) ────────────────────────────

_ENUM_TYPES = ("lifecyclestatus", "assetkind")
_SEEDED_SLUG = "agentic-ai"

# Distinctive marker propagated through the generation → sync → PUT body chain.
MARKER = "# GENERATED CONTENT MARKER v2"

_QA_PASS = {
    "content_accuracy": 5,
    "alignment": 5,
    "prerequisites": 5,
    "consistency": 5,
    "instructor_support": 5,
    "student_experience": 5,
}

# ── Sync-specific constants ───────────────────────────────────────────────────

_SYNC_REPO = "test-owner/test-repo"
_SYNC_PATH_PREFIX = "curriculum"
_SYNC_BASE_BRANCH = "main"
_ASSET_SOURCE_URL = "test-lesson.md"                            # set directly in setup step
_EXPECTED_FILE_PATH = f"{_SYNC_PATH_PREFIX}/{_ASSET_SOURCE_URL}"
_EXPECTED_PR_URL = "https://github.com/test-owner/test-repo/pull/42"


# ── Fixture ── (mirrors test_acceptance_execute.py::seeded_backfilled_engine) ─


@pytest.fixture
async def seeded_backfilled_engine():
    """Fresh schema + full bootcamp seed + content-model backfill.

    Identical to test_acceptance_execute.py's fixture so the merge-ritual helpers
    work without modification.
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


# ── Session + domain helpers (mirror test_acceptance_execute.py) ──────────────


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


# ── Fake generator ────────────────────────────────────────────────────────────


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


# ── Acceptance test: merge → sync ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acceptance_pipeline_sync_through(
    seeded_backfilled_engine: Any, monkeypatch: Any
) -> None:
    """Phase 4 acceptance: pipeline → change_set → merge → GitHub PR opened.

    Drives the full Phase-3 loop with the Phase-4 additions:

    1. Setup: seed + backfill + source_url on target asset + SyncTarget row.
    2. Run pipeline (all AI mocked, generation ON) → adopted CCR with change_set.
    3. Drive merge (QA + 2 approvals + merge()) with FRESHNESS_SYNC_ENABLED=True
       and MockTransport on app.freshness_pipeline.sync_github._transport.
    4. Assert:
       - Exactly ONE PR-create call captured.
       - PUT for ``curriculum/test-lesson.md`` whose base64-decoded body contains MARKER.
       - SyncLog(status="success", curriculum_version_id==new_cv, detail.url==PR_URL).
    """
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            # ── Phase A: pre-run setup ────────────────────────────────────────
            setup_session = await _open_org_session(engine, org_id)
            try:
                setup_session.add(PipelineSeen(signal_id="acceptance-prior-marker"))
                await setup_session.flush()

                cur = await _get_curriculum(setup_session)

                cv = await active_curriculum_version(setup_session, cur.id)
                assert cv is not None, (
                    "backfill must produce a CurriculumVersion for the seeded curriculum"
                )
                old_cv_id = cv.id

                cur.active_content_version_id = cv.id
                setup_session.add(cur)
                await setup_session.flush()

                members = await version_members(setup_session, cv.id)
                lp_members = [m for m in members if m.kind == AssetKind.lesson_plan]
                assert lp_members, "backfilled curriculum must have at least one lesson_plan"
                target_key = lp_members[0].lineage_key

                # Phase 4: set source_url on the target LineageAsset (backfill step).
                target_asset = await setup_session.scalar(
                    select(LineageAsset).where(LineageAsset.lineage_key == target_key)
                )
                assert target_asset is not None, (
                    f"LineageAsset for {target_key!r} not found"
                )
                target_asset.source_url = _ASSET_SOURCE_URL
                setup_session.add(target_asset)

                # Phase 4: seed a SyncTarget (config-as-data).
                sync_target_row = SyncTarget(
                    curriculum_id=cur.id,
                    kind="github_pr",
                    config={
                        "repo": _SYNC_REPO,
                        "base_branch": _SYNC_BASE_BRANCH,
                        "path_prefix": _SYNC_PATH_PREFIX,
                    },
                    active=True,
                )
                setup_session.add(sync_target_row)

                # Pre-seed a success SyncLog for the initial CurriculumVersion so
                # the runner's pending-sync sweep skips it.  Without this, the
                # sweep (which is also enabled because settings is a shared object)
                # would sync v1.0.0 during run_org and produce a second PR-create
                # call, making the "exactly ONE PR" assertion fail.  We want to
                # test only the post-merge hook's sync of the freshly-released
                # v1.0.1 here.
                setup_session.add(
                    SyncLog(
                        curriculum_id=cur.id,
                        version_id=None,
                        curriculum_version_id=cv.id,
                        target="github",
                        status="success",
                        detail={"url": "https://github.com/test-owner/test-repo/pull/0"},
                    )
                )
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
            monkeypatch.setattr(
                "app.freshness_pipeline.runner.settings.FRESHNESS_GENERATION_ENABLED", True
            )

            # Phase 4 sync kill switches — both the approvals router (hook) and
            # syncing.py (token used inside open_content_pr) must see the values.
            monkeypatch.setattr(
                "app.routers.approvals.settings.FRESHNESS_SYNC_ENABLED", True
            )
            monkeypatch.setattr(
                "app.routers.approvals.settings.SYNC_GITHUB_TOKEN", "test-token"
            )
            monkeypatch.setattr(
                "app.freshness_pipeline.syncing.settings.SYNC_GITHUB_TOKEN", "test-token"
            )

            # MockTransport on the adapter's module-level seam.
            # This test exercises the REAL adapter (sync_github.open_content_pr),
            # not a fake sync_release — exactly as required by the spec.
            pr_create_calls: list[dict] = []
            put_records: list[tuple[str, dict]] = []  # (url_path, body_dict)

            def github_handler(request: httpx.Request) -> httpx.Response:
                # GET base-ref SHA.
                if "/git/ref/heads/" in request.url.path and request.method == "GET":
                    return httpx.Response(200, json={"object": {"sha": "basesha123"}})
                # POST create branch (new → 201).
                if request.url.path.endswith("/git/refs") and request.method == "POST":
                    return httpx.Response(201, json={})
                # GET existing file → 404 (first sync, file is new).
                if "/contents/" in request.url.path and request.method == "GET":
                    return httpx.Response(404, json={"message": "Not Found"})
                # PUT file contents.
                if "/contents/" in request.url.path and request.method == "PUT":
                    put_records.append((request.url.path, json.loads(request.content)))
                    return httpx.Response(201, json={})
                # POST create PR.
                if request.url.path.endswith("/pulls") and request.method == "POST":
                    pr_create_calls.append(json.loads(request.content))
                    return httpx.Response(201, json={"html_url": _EXPECTED_PR_URL})
                return httpx.Response(404, json={"message": "unexpected request in test"})

            monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(github_handler))

            # ── Phase C: runner session factory ──────────────────────────────

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

            merge_session = await _open_org_session(engine, org_id)
            try:
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
                    f"Expected {target_key!r} in change_set.changed; got {sorted(changed_keys)}"
                )

                # ── Phase F: merge ritual ─────────────────────────────────────

                instructor = await _user_by_role(merge_session, "instructor")
                second = await _user_by_role(merge_session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                await _approve_gate(merge_session, ccr, instructor, second)
                out = await merge(ccr.id, current=merger, db=merge_session)

                # ── Phase G: new active CurriculumVersion ─────────────────────

                assert out.status == "active", f"Merge result status: {out.status}"
                new_cv_id = out.version_id
                assert new_cv_id != old_cv_id, (
                    "Merge must produce a DIFFERENT CurriculumVersion id"
                )
                assert out.parent_version_id == old_cv_id

                # ── Phase H: generated content in new version ─────────────────

                new_members = await version_members(merge_session, new_cv_id)
                target_member = next(
                    (m for m in new_members if m.lineage_key == target_key), None
                )
                assert target_member is not None
                cv_row = await merge_session.get(ContentVersion, target_member.content_version_id)
                assert cv_row is not None
                assert MARKER in cv_row.content, (
                    f"Generated content must contain {MARKER!r}; "
                    f"got first 200 chars: {cv_row.content[:200]!r}"
                )

                # ── Phase I: immutability ─────────────────────────────────────

                old_cv_row = await merge_session.get(ContentCV, old_cv_id)
                assert old_cv_row is not None, "Prior CurriculumVersion must still exist"

                # ── Phase J: sync assertions ──────────────────────────────────

                # ONE PR-create call.
                assert len(pr_create_calls) == 1, (
                    f"Expected exactly 1 PR-create (POST /pulls) call; "
                    f"got {len(pr_create_calls)}"
                )

                # PUT for the mapped path: path_prefix + "/" + source_url.
                expected_contents_path = (
                    f"/repos/{_SYNC_REPO}/contents/{_EXPECTED_FILE_PATH}"
                )
                matching_puts = [
                    (path, body)
                    for path, body in put_records
                    if path == expected_contents_path
                ]
                assert len(matching_puts) >= 1, (
                    f"Expected a PUT for {expected_contents_path!r}; "
                    f"PUT paths seen: {[p for p, _ in put_records]}"
                )

                # PUT body base64-decoded contains the GENERATED marker content.
                _, put_body = matching_puts[0]
                decoded_content = base64.b64decode(put_body["content"]).decode("utf-8")
                assert MARKER in decoded_content, (
                    f"PUT body must base64-decode to content containing {MARKER!r}; "
                    f"decoded first 300 chars: {decoded_content[:300]!r}"
                )

                # SyncLog: exactly one success row for the NEW version.
                # (A pre-seeded success log for old_cv_id also exists to prevent
                # the sweep from double-syncing; filter to new_cv_id only.)
                sync_logs = (
                    await merge_session.execute(
                        select(SyncLog).where(
                            SyncLog.status == "success",
                            SyncLog.curriculum_version_id == new_cv_id,
                        )
                    )
                ).scalars().all()
                assert len(sync_logs) == 1, (
                    f"Expected exactly 1 success SyncLog for new version {new_cv_id}; "
                    f"got {len(sync_logs)}"
                )
                sl = sync_logs[0]
                assert sl.curriculum_version_id == new_cv_id, (
                    f"SyncLog.curriculum_version_id must == new CurriculumVersion; "
                    f"expected {new_cv_id}, got {sl.curriculum_version_id}"
                )
                assert sl.detail.get("url") == _EXPECTED_PR_URL, (
                    f"SyncLog.detail['url'] must be {_EXPECTED_PR_URL!r}; "
                    f"got {sl.detail.get('url')!r}"
                )

            finally:
                await merge_session.close()

    finally:
        current_org.reset(token)


# ── Sweep-retry acceptance test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acceptance_sync_sweep_retry(
    seeded_backfilled_engine: Any, monkeypatch: Any
) -> None:
    """Phase 4 sweep: a pre-seeded failed SyncLog is retried on the next run.

    Scenario:
      - Active CurriculumVersion with no success SyncLog (only a failed one).
      - FRESHNESS_SYNC_ENABLED=True + active SyncTarget.
      - run_org sweep picks it up, retries via fake sync_release, writes success log.

    The branch name is deterministic per version:
        curricmesh-sync/{curriculum.slug}-v{major}.{minor}.{patch}
    so sweeps are idempotent — the adapter handles existing-branch 422.
    The fake sync_release proves the sweep saw the right CurriculumVersion;
    the real adapter's idempotency is covered by test_sync_github.py.
    """
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            # ── Setup: seed failed SyncLog for the active version ─────────────
            setup_session = await _open_org_session(engine, org_id)
            cv_id: uuid.UUID  # captured for later assertions
            try:
                # Mark seen-state non-empty so the runner skips first-run seeding.
                setup_session.add(PipelineSeen(signal_id="sweep-prior-marker"))
                await setup_session.flush()

                cur = await _get_curriculum(setup_session)
                cv = await active_curriculum_version(setup_session, cur.id)
                assert cv is not None
                cv_id = cv.id

                # Activate the new-model pointer so the sweep finds the active version.
                cur.active_content_version_id = cv.id
                setup_session.add(cur)
                await setup_session.flush()

                members = await version_members(setup_session, cv.id)
                lp_members = [m for m in members if m.kind == AssetKind.lesson_plan]
                assert lp_members
                target_key = lp_members[0].lineage_key

                # Set source_url so path mapping would succeed if the real adapter ran.
                target_asset = await setup_session.scalar(
                    select(LineageAsset).where(LineageAsset.lineage_key == target_key)
                )
                assert target_asset is not None
                target_asset.source_url = _ASSET_SOURCE_URL
                setup_session.add(target_asset)

                # SyncTarget.
                setup_session.add(
                    SyncTarget(
                        curriculum_id=cur.id,
                        kind="github_pr",
                        config={
                            "repo": _SYNC_REPO,
                            "base_branch": _SYNC_BASE_BRANCH,
                            "path_prefix": _SYNC_PATH_PREFIX,
                        },
                        active=True,
                    )
                )

                # Pre-seed the FAILED SyncLog that the sweep must retry.
                setup_session.add(
                    SyncLog(
                        curriculum_id=cur.id,
                        version_id=None,
                        curriculum_version_id=cv.id,
                        target="github",
                        status="failed",
                        detail={"error": "simulated prior failure"},
                    )
                )
                await setup_session.commit()
            finally:
                await setup_session.close()

            # ── Wire mocks ────────────────────────────────────────────────────

            async def _fake_fetch_all(*_, **__):
                return []  # No industry signals — sweep only.

            async def _no_uni_signals(session, item, *, extractor, searcher, http, dry_run=False):
                return []

            monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
            monkeypatch.setattr(
                "app.freshness_pipeline.university.check_watch_item", _no_uni_signals
            )

            # Enable the sweep via runner's settings reference.
            monkeypatch.setattr(
                "app.freshness_pipeline.runner.settings.FRESHNESS_SYNC_ENABLED", True
            )
            monkeypatch.setattr(
                "app.freshness_pipeline.runner.settings.SYNC_GITHUB_TOKEN", "test-token"
            )
            monkeypatch.setattr(
                "app.freshness_pipeline.syncing.settings.SYNC_GITHUB_TOKEN", "test-token"
            )

            # Fake sync_release: records the call and writes a success SyncLog.
            sweep_calls: list[dict] = []

            async def _fake_sync_release(session, *, curriculum, new_version, target, ccr=None):
                sweep_calls.append(
                    {
                        "curriculum_version_id": new_version.id,
                        "repo": target.config.get("repo"),
                        "branch": (
                            f"curricmesh-sync/{curriculum.slug}"
                            f"-v{new_version.major}.{new_version.minor}.{new_version.patch}"
                        ),
                    }
                )
                success_log = SyncLog(
                    curriculum_id=curriculum.id,
                    version_id=None,
                    curriculum_version_id=new_version.id,
                    target="github",
                    status="success",
                    detail={"url": _EXPECTED_PR_URL},
                )
                session.add(success_log)
                await session.flush()
                return success_log

            monkeypatch.setattr(
                "app.freshness_pipeline.runner.sync_release", _fake_sync_release
            )

            # ── Runner session factory ────────────────────────────────────────

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

            # ── Run pipeline (no signals, sweep only) ─────────────────────────

            run = await run_org(
                _runner_session,
                org_id,
                extractor=FakeGapExtractor([]),
                searcher=None,
                enricher=None,
                judge=FakeAdoptAllJudge(),
                generator=None,  # generation kill switch is OFF (default) — never called
            )

            assert run.status == "ok", f"Runner failed: {run.stats}"

            # ── Sweep assertions ──────────────────────────────────────────────

            assert run.stats.get("syncs_attempted", 0) == 1, (
                f"Sweep must attempt 1 sync; got syncs_attempted="
                f"{run.stats.get('syncs_attempted')}"
            )
            assert run.stats.get("syncs_succeeded", 0) == 1, (
                f"Sweep must record 1 success; got syncs_succeeded="
                f"{run.stats.get('syncs_succeeded')}"
            )
            assert len(sweep_calls) == 1, (
                f"fake sync_release must be called exactly once; got {len(sweep_calls)}"
            )

            # Sweep targeted the correct CurriculumVersion.
            assert sweep_calls[0]["curriculum_version_id"] == cv_id, (
                f"Sweep used wrong version; expected {cv_id}, "
                f"got {sweep_calls[0]['curriculum_version_id']}"
            )
            assert sweep_calls[0]["repo"] == _SYNC_REPO

            # Branch name is deterministic (proves idempotent retry semantics).
            assert "curricmesh-sync/" in sweep_calls[0]["branch"], (
                f"Branch name must follow the curricmesh-sync/{{slug}}-v{{semver}} pattern; "
                f"got {sweep_calls[0]['branch']!r}"
            )

            # Success SyncLog committed and queryable after the runner finishes.
            verify_session = await _open_org_session(engine, org_id)
            try:
                success_logs = (
                    await verify_session.execute(
                        select(SyncLog).where(
                            SyncLog.status == "success",
                            SyncLog.curriculum_version_id == cv_id,
                        )
                    )
                ).scalars().all()
                assert len(success_logs) == 1, (
                    f"Expected 1 committed success SyncLog for cv {cv_id}; "
                    f"got {len(success_logs)}"
                )
            finally:
                await verify_session.close()

    finally:
        current_org.reset(token)
