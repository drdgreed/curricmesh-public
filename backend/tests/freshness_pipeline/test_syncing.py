"""Tests for ``app.freshness_pipeline.syncing.sync_release``.

Seeding pattern reuses the immutable-model helpers from
``test_content_cards.py`` (same approach as ``test_generation.py``).

The GitHub PR adapter is replaced by a fake via monkeypatch of
``syncing.open_content_pr`` — no HTTP required.

Test matrix
-----------
1. changed_members_only   — only changed / new members ship; unchanged skip.
2. source_url_mapping     — source_url path includes prefix with rstrip of "/".
3. path_rules_fallback    — assets without source_url match path_rules template.
4. unmapped_detail        — unmapped assets appear in SyncLog.detail + PR body.
5. no_mappable_skipped    — all unmapped → status="skipped", adapter NOT called.
6. adapter_raises_failed  — adapter RuntimeError → status="failed", not raised.

All logs assert curriculum_version_id == new_version.id and version_id is None.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import freshness_pipeline
from app.freshness_pipeline import syncing
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.sync import SyncLog, SyncTarget

# Re-use immutable-model seeding helpers (plain async functions, no fixtures).
from tests.freshness_pipeline.test_content_cards import (
    _make_content_version,
    _make_curriculum,
    _make_curriculum_version,
    _make_lineage,
    _make_member,
)


# ---------------------------------------------------------------------------
# Local seeding helpers
# ---------------------------------------------------------------------------


async def _make_cv_with_parent(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    parent_version_id: uuid.UUID | None,
    major: int = 1,
    minor: int = 0,
    patch: int = 0,
) -> CurriculumVersion:
    """Insert a CurriculumVersion, optionally pointing at a parent."""
    cv = CurriculumVersion(
        curriculum_id=curriculum_id,
        major=major,
        minor=minor,
        patch=patch,
        status=LifecycleStatus.active,
        parent_version_id=parent_version_id,
    )
    session.add(cv)
    await session.flush()
    return cv


async def _make_target(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    config: dict,
) -> SyncTarget:
    target = SyncTarget(
        curriculum_id=curriculum_id,
        kind="github_pr",
        config=config,
        active=True,
    )
    session.add(target)
    await session.flush()
    return target


def _fake_pr(url: str = "https://github.com/org/repo/pull/1") -> AsyncMock:
    """Return an AsyncMock that resolves to *url*."""
    return AsyncMock(return_value=url)


# ---------------------------------------------------------------------------
# Test 1 — only changed / new members ship
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_changed_members_only(db_session: AsyncSession, monkeypatch):
    """Only the changed + new members appear in files; the unchanged one is absent."""
    fake_pr = _fake_pr()
    monkeypatch.setattr(syncing, "open_content_pr", fake_pr)

    cur = await _make_curriculum(db_session)

    # Parent version: assets A (unchanged) + B (will change)
    parent_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)
    asset_a = await _make_lineage(db_session, key="wk01/lesson_plan")
    asset_b = await _make_lineage(db_session, key="wk01/assessment")

    content_a1 = await _make_content_version(db_session, asset=asset_a, seq=1, body="Content A v1")
    content_b1 = await _make_content_version(db_session, asset=asset_b, seq=1, body="Content B v1")

    # Set source_url so they map
    asset_a.source_url = "wk01/lesson_plan.md"
    asset_b.source_url = "wk01/assessment.md"
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=parent_cv.id,
        asset=asset_a,
        content_version=content_a1,
        section="Week 1",
        week_index=1,
        order=0,
    )
    await _make_member(
        db_session,
        curriculum_version_id=parent_cv.id,
        asset=asset_b,
        content_version=content_b1,
        section="Week 1",
        week_index=1,
        order=1,
    )

    # Child version: A unchanged, B updated, C new
    new_cv = await _make_cv_with_parent(
        db_session,
        curriculum_id=cur.id,
        parent_version_id=parent_cv.id,
        major=1, minor=1, patch=0,
    )
    asset_c = await _make_lineage(db_session, key="wk02/lesson_plan")
    content_b2 = await _make_content_version(db_session, asset=asset_b, seq=2, body="Content B v2")
    content_c1 = await _make_content_version(db_session, asset=asset_c, seq=1, body="Content C v1")
    asset_c.source_url = "wk02/lesson_plan.md"
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=new_cv.id,
        asset=asset_a,
        content_version=content_a1,  # same cv → unchanged
        section="Week 1",
        week_index=1,
        order=0,
    )
    await _make_member(
        db_session,
        curriculum_version_id=new_cv.id,
        asset=asset_b,
        content_version=content_b2,  # new cv → changed
        section="Week 1",
        week_index=1,
        order=1,
    )
    await _make_member(
        db_session,
        curriculum_version_id=new_cv.id,
        asset=asset_c,
        content_version=content_c1,  # new asset → changed
        section="Week 2",
        week_index=2,
        order=0,
    )

    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={"repo": "org/repo", "path_prefix": "content"},
    )

    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=new_cv, target=target
    )

    assert sync_log.status == "success"
    assert sync_log.curriculum_version_id == new_cv.id
    assert sync_log.version_id is None

    # open_content_pr was called exactly once
    fake_pr.assert_awaited_once()
    call_kwargs = fake_pr.call_args.kwargs

    # Only B and C shipped — A was unchanged
    shipped_paths = set(call_kwargs["files"].keys())
    assert "content/wk01/assessment.md" in shipped_paths
    assert "content/wk02/lesson_plan.md" in shipped_paths
    assert "content/wk01/lesson_plan.md" not in shipped_paths

    # Content is correct
    assert call_kwargs["files"]["content/wk01/assessment.md"] == "Content B v2"
    assert call_kwargs["files"]["content/wk02/lesson_plan.md"] == "Content C v1"


# ---------------------------------------------------------------------------
# Test 2 — source_url mapping incl. path_prefix rstrip("/")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_url_mapping_with_trailing_slash(db_session: AsyncSession, monkeypatch):
    """source_url path = path_prefix.rstrip('/') + '/' + source_url (no double slash)."""
    fake_pr = _fake_pr()
    monkeypatch.setattr(syncing, "open_content_pr", fake_pr)

    cur = await _make_curriculum(db_session)
    root_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)

    asset = await _make_lineage(db_session, key="M0.1-llm")
    content = await _make_content_version(db_session, asset=asset, seq=1, body="LLM content")
    asset.source_url = "M0.1-llm-mental-model.md"
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset,
        content_version=content,
        section="Week 0",
        week_index=0,
        order=0,
    )

    # Trailing slash on path_prefix — must not produce double slash
    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={"repo": "org/repo", "path_prefix": "content/modules/"},
    )

    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=root_cv, target=target
    )

    assert sync_log.status == "success"
    shipped_paths = list(fake_pr.call_args.kwargs["files"].keys())
    assert len(shipped_paths) == 1
    assert shipped_paths[0] == "content/modules/M0.1-llm-mental-model.md"


# ---------------------------------------------------------------------------
# Test 3 — path_rules fallback with template substitution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_rules_fallback(db_session: AsyncSession, monkeypatch):
    """Assets without source_url match path_rules; template vars substituted."""
    fake_pr = _fake_pr()
    monkeypatch.setattr(syncing, "open_content_pr", fake_pr)

    cur = await _make_curriculum(db_session)
    root_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)

    asset = await _make_lineage(db_session, key="wk03/lesson_plan", kind=AssetKind.lesson_plan)
    content = await _make_content_version(db_session, asset=asset, seq=1, body="Lesson body")
    # No source_url — uses path_rules
    assert asset.source_url is None
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset,
        content_version=content,
        section="Week 3",
        week_index=3,
        order=0,
    )

    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={
            "repo": "org/repo",
            "path_prefix": "content",
            "path_rules": [
                {
                    "kind": "lesson_plan",
                    "path_template": "modules/{lineage_key}/week{week_index}.md",
                }
            ],
        },
    )

    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=root_cv, target=target
    )

    assert sync_log.status == "success"
    shipped_paths = list(fake_pr.call_args.kwargs["files"].keys())
    assert len(shipped_paths) == 1
    assert shipped_paths[0] == "modules/wk03/lesson_plan/week3.md"


# ---------------------------------------------------------------------------
# Test 4 — unmapped assets appear in detail + PR body warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmapped_in_detail_and_pr_body(db_session: AsyncSession, monkeypatch):
    """An unmapped asset appears in SyncLog.detail['unmapped'] AND the PR body."""
    fake_pr = _fake_pr()
    monkeypatch.setattr(syncing, "open_content_pr", fake_pr)

    cur = await _make_curriculum(db_session)
    root_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)

    # Mapped asset
    asset_a = await _make_lineage(db_session, key="wk01/lesson_plan")
    content_a = await _make_content_version(db_session, asset=asset_a, seq=1, body="Lesson content")
    asset_a.source_url = "wk01/lesson.md"

    # Unmapped asset (no source_url, no matching rule)
    asset_b = await _make_lineage(db_session, key="wk01/extra", kind=AssetKind.assessment)
    content_b = await _make_content_version(db_session, asset=asset_b, seq=1, body="Extra content")
    assert asset_b.source_url is None

    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset_a,
        content_version=content_a,
        section="Week 1",
        week_index=1,
        order=0,
    )
    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset_b,
        content_version=content_b,
        section="Week 1",
        week_index=1,
        order=1,
    )

    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={
            "repo": "org/repo",
            "path_prefix": "content",
            # No path_rules → asset_b is unmapped
        },
    )

    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=root_cv, target=target
    )

    assert sync_log.status == "success"
    assert sync_log.curriculum_version_id == root_cv.id
    assert sync_log.version_id is None

    # Unmapped asset recorded in detail
    assert "wk01/extra" in sync_log.detail["unmapped"]

    # PR body contains the unmapped assets section
    pr_body: str = fake_pr.call_args.kwargs["body"]
    assert "## Unmapped assets" in pr_body
    assert "wk01/extra" in pr_body


# ---------------------------------------------------------------------------
# Test 5 — no mappable files → status="skipped", adapter NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_mappable_files_skipped(db_session: AsyncSession, monkeypatch):
    """All members unmapped → SyncLog(status='skipped'), open_content_pr not called."""
    fake_pr = _fake_pr()
    monkeypatch.setattr(syncing, "open_content_pr", fake_pr)

    cur = await _make_curriculum(db_session)
    root_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)

    asset = await _make_lineage(db_session, key="wk01/lesson_plan")
    content = await _make_content_version(db_session, asset=asset, seq=1, body="Body")
    # No source_url, no path_rules configured → unmapped
    assert asset.source_url is None
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={"repo": "org/repo", "path_prefix": "content"},
    )

    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=root_cv, target=target
    )

    assert sync_log.status == "skipped"
    assert sync_log.curriculum_version_id == root_cv.id
    assert sync_log.version_id is None
    assert sync_log.detail["reason"] == "no mappable files"
    assert "wk01/lesson_plan" in sync_log.detail["unmapped"]

    # Adapter must NOT have been called
    fake_pr.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 6 — adapter raising → status="failed", error in detail, never raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_raises_returns_failed_log(db_session: AsyncSession, monkeypatch):
    """open_content_pr raising → SyncLog(status='failed') returned, not propagated."""
    raising_pr = AsyncMock(side_effect=RuntimeError("GitHub API unreachable"))
    monkeypatch.setattr(syncing, "open_content_pr", raising_pr)

    cur = await _make_curriculum(db_session)
    root_cv = await _make_cv_with_parent(db_session, curriculum_id=cur.id, parent_version_id=None)

    asset = await _make_lineage(db_session, key="wk01/lesson_plan")
    content = await _make_content_version(db_session, asset=asset, seq=1, body="Content")
    asset.source_url = "wk01/lesson.md"
    await db_session.flush()

    await _make_member(
        db_session,
        curriculum_version_id=root_cv.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    target = await _make_target(
        db_session,
        curriculum_id=cur.id,
        config={"repo": "org/repo", "path_prefix": "content"},
    )

    # Must not raise
    sync_log = await syncing.sync_release(
        db_session, curriculum=cur, new_version=root_cv, target=target
    )

    assert sync_log.status == "failed"
    assert sync_log.curriculum_version_id == root_cv.id
    assert sync_log.version_id is None
    assert "GitHub API unreachable" in sync_log.detail["error"]
