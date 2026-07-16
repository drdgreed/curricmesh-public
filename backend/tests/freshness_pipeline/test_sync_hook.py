"""Tests for the post-merge sync hook in app.routers.approvals.merge().

Three cases:
  (a) FRESHNESS_SYNC_ENABLED=True + token set → sync_release called once with
      the new CurriculumVersion; the returned SyncLog is committed.
  (b) FRESHNESS_SYNC_ENABLED=False → sync_release not called; merge still ok.
  (c) sync_release raises → merge still returns success (hook is best-effort).

Setup uses the db_session fixture (minimal content model, no full seed) so
tests run fast.  The merge path (fork → CCR activate) is driven engine-direct,
same as tests/merge/test_merge.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workflow.engine import record_approval, record_qa
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.sync import SyncLog, SyncTarget
from app.models.user import User
from app.models.workflow import ChangeRequest
from app.routers.approvals import merge
from tests.conftest import DEFAULT_ORG_ID  # noqa: F401

# Reuse content-model seeding helpers (no fixtures, plain async functions).
from tests.freshness_pipeline.test_content_cards import (
    _make_content_version,
    _make_curriculum_version,
    _make_lineage,
    _make_member,
)

# ---------------------------------------------------------------------------
# QA dimension scores (all 5s)
# ---------------------------------------------------------------------------

_QA_PASS = {
    "content_accuracy": 5,
    "alignment": 5,
    "prerequisites": 5,
    "consistency": 5,
    "instructor_support": 5,
    "student_experience": 5,
}


# ---------------------------------------------------------------------------
# Shared setup helper
# ---------------------------------------------------------------------------


async def _setup_merge_scenario(
    session: AsyncSession,
) -> tuple[Curriculum, CurriculumVersion, ChangeRequest, User, User]:
    """Seed a minimal content model + CCR ready for merge().

    Returns (curriculum, active_cv, ccr, instructor_user, architect_user).
    The CCR carries an empty patch-bump change_set (pure snapshot — valid for
    fork(), no LineageAsset changes required).
    """
    # Users (nullable FK — approvals only need distinct approver_ids)
    instructor = User(email=f"instructor-{uuid.uuid4().hex[:6]}@test.com", role="instructor")
    architect = User(email=f"architect-{uuid.uuid4().hex[:6]}@test.com", role="architect")
    session.add_all([instructor, architect])
    await session.flush()

    # Curriculum (active_content_version_id set below)
    cur = Curriculum(name="Hook Test Curriculum", slug=f"hook-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    # CurriculumVersion + one member so fork() has content to snapshot
    cv = await _make_curriculum_version(session, curriculum_id=cur.id)
    asset = await _make_lineage(session, key="wk01/hook-test", kind=AssetKind.lesson_plan)
    content = await _make_content_version(session, asset=asset, seq=1, body="Hook test content body")
    await _make_member(
        session,
        curriculum_version_id=cv.id,
        asset=asset,
        content_version=content,
        section="test-section",
        week_index=1,
        order=1,
    )

    # Activate the new-model pointer so fork() resolves an active version
    cur.active_content_version_id = cv.id
    session.add(cur)
    await session.flush()

    # CCR: empty change_set = pure snapshot (patch bump, no content changes)
    ccr = ChangeRequest(
        curriculum_id=cur.id,
        title="[TEST] Hook test CCR",
        change_set={
            "bump": "patch",
            "changed": [],
            "added": [],
            "removed": [],
            "edges_added": [],
            "edges_removed": [],
        },
        status=LifecycleStatus.draft,
    )
    session.add(ccr)
    await session.flush()

    return cur, cv, ccr, instructor, architect


async def _approve_ccr(
    session: AsyncSession,
    ccr: ChangeRequest,
    instructor: User,
    architect: User,
) -> None:
    """Satisfy can_release(): one passing QA + two distinct approvals."""
    await record_qa(
        session, ccr=ccr, reviewer_id=instructor.id, dimension_scores=_QA_PASS, verdict="pass"
    )
    await record_approval(
        session, ccr=ccr, approver_id=instructor.id, role="instructor", decision="approve"
    )
    await record_approval(
        session, ccr=ccr, approver_id=architect.id, role="architect", decision="approve"
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Test (a): hook fires when FRESHNESS_SYNC_ENABLED=True + token set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_hook_fires_when_enabled(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Hook calls sync_release once per active SyncTarget; SyncLog is committed."""
    cur, cv, ccr, instructor, architect = await _setup_merge_scenario(db_session)
    await _approve_ccr(db_session, ccr, instructor, architect)

    # Add one active SyncTarget for this curriculum
    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "org/repo", "base_branch": "main", "path_prefix": "curriculum"},
        active=True,
    )
    db_session.add(target)
    await db_session.flush()

    # Monkeypatch sync_release — records call, adds a success SyncLog
    sync_calls: list[dict] = []

    async def _fake_sync_release(session, *, curriculum, new_version, target, ccr=None):
        sync_calls.append(
            {"curriculum_id": curriculum.id, "new_version_id": new_version.id}
        )
        log = SyncLog(
            curriculum_id=curriculum.id,
            version_id=None,
            curriculum_version_id=new_version.id,
            target="github",
            status="success",
            detail={"url": "https://github.com/org/repo/pull/1"},
        )
        session.add(log)
        await session.flush()
        return log

    monkeypatch.setattr("app.routers.approvals.sync_release", _fake_sync_release)
    monkeypatch.setattr("app.routers.approvals.settings.FRESHNESS_SYNC_ENABLED", True)
    monkeypatch.setattr("app.routers.approvals.settings.SYNC_GITHUB_TOKEN", "test-token")

    merger = {"sub": str(architect.id), "role": "architect"}
    result = await merge(ccr.id, current=merger, db=db_session)

    assert result.status == "active", f"Unexpected merge status: {result.status}"

    # sync_release called exactly once (one active target)
    assert len(sync_calls) == 1
    assert sync_calls[0]["curriculum_id"] == cur.id
    # new_version must be the NEWLY forked version (different from the old cv)
    assert sync_calls[0]["new_version_id"] == result.version_id

    # SyncLog committed and queryable
    logs = (await db_session.execute(select(SyncLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].curriculum_version_id == result.version_id


# ---------------------------------------------------------------------------
# Test (b): hook does NOT fire when FRESHNESS_SYNC_ENABLED=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_hook_skips_when_disabled(
    db_session: AsyncSession, monkeypatch
) -> None:
    """sync_release is never called when the kill switch is off."""
    cur, cv, ccr, instructor, architect = await _setup_merge_scenario(db_session)
    await _approve_ccr(db_session, ccr, instructor, architect)

    sync_called = False

    async def _raise_if_called(*args, **kwargs):
        nonlocal sync_called
        sync_called = True

    monkeypatch.setattr("app.routers.approvals.sync_release", _raise_if_called)
    monkeypatch.setattr("app.routers.approvals.settings.FRESHNESS_SYNC_ENABLED", False)

    merger = {"sub": str(architect.id), "role": "architect"}
    result = await merge(ccr.id, current=merger, db=db_session)

    assert result.status == "active"
    assert not sync_called, "sync_release must not be called when FRESHNESS_SYNC_ENABLED is False"


# ---------------------------------------------------------------------------
# Test (c): merge succeeds even when sync_release raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_succeeds_when_sync_raises(
    db_session: AsyncSession, monkeypatch
) -> None:
    """A failing sync_release must not fail the merge — hook is best-effort."""
    cur, cv, ccr, instructor, architect = await _setup_merge_scenario(db_session)
    await _approve_ccr(db_session, ccr, instructor, architect)

    # Add a SyncTarget so the hook actually runs before raising
    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "org/repo", "base_branch": "main", "path_prefix": "curriculum"},
        active=True,
    )
    db_session.add(target)
    await db_session.flush()

    async def _always_raise(session, *, curriculum, new_version, target, ccr=None):
        raise RuntimeError("Simulated sync failure from test")

    monkeypatch.setattr("app.routers.approvals.sync_release", _always_raise)
    monkeypatch.setattr("app.routers.approvals.settings.FRESHNESS_SYNC_ENABLED", True)
    monkeypatch.setattr("app.routers.approvals.settings.SYNC_GITHUB_TOKEN", "test-token")

    merger = {"sub": str(architect.id), "role": "architect"}
    # Must not raise — sync failure is swallowed
    result = await merge(ccr.id, current=merger, db=db_session)

    assert result.status == "active", (
        f"Merge must succeed even when sync raises; got status={result.status!r}"
    )
