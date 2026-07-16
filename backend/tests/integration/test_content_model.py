"""Integration tests for the immutable content model (Task 1.1).

Covers, against the real Postgres schema under the DEFAULT_ORG tenant context:
  - a row round-trips for each of the 5 new tables (insert → select back);
  - the ContentVersion immutability guard refuses an UPDATE (append-only);
  - content_hash determinism holds when persisted on a real row.

These tables are additive and unread by any application path; the test only
proves they persist and that the write-once guard fires.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    ImmutableContentVersionError,
    LineageAsset,
    VersionEdge,
    VersionMember,
)


async def _seed_curriculum(session: AsyncSession) -> Curriculum:
    cur = Curriculum(name="AI Eng", slug=f"ai-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()
    return cur


async def _seed_two_assets(session: AsyncSession) -> tuple[LineageAsset, LineageAsset]:
    a = LineageAsset(kind=AssetKind.lesson_plan, lineage_key="wk01/lesson_plan")
    b = LineageAsset(kind=AssetKind.rubric, lineage_key="wk01/rubric", source_url="http://x")
    session.add_all([a, b])
    await session.flush()
    return a, b


@pytest.mark.asyncio
async def test_all_five_tables_round_trip(db_session: AsyncSession):
    cur = await _seed_curriculum(db_session)
    asset_a, asset_b = await _seed_two_assets(db_session)

    # ContentVersion (immutable blob) for each asset.
    ch = content_hash("lesson_plan", "the body", {"k": "v"})
    cv = ContentVersion(
        asset_id=asset_a.id,
        seq=1,
        content="the body",
        metadata_={"k": "v"},
        content_hash=ch,
    )
    db_session.add(cv)
    await db_session.flush()

    # CurriculumVersion manifest.
    cversion = CurriculumVersion(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    db_session.add(cversion)
    await db_session.flush()

    # VersionMember: asset_a's content placed in the version.
    member = VersionMember(
        curriculum_version_id=cversion.id,
        asset_id=asset_a.id,
        asset_version_id=cv.id,
        section="Week 1",
        week_index=1,
        order=0,
    )
    # VersionEdge: asset_a → asset_b prerequisite.
    edge = VersionEdge(
        curriculum_version_id=cversion.id,
        from_asset_id=asset_a.id,
        to_asset_id=asset_b.id,
        edge_type="prerequisite",
        validated_against_seq=None,
    )
    db_session.add_all([member, edge])
    await db_session.flush()

    # Read each back.
    got_asset = (
        await db_session.execute(select(LineageAsset).where(LineageAsset.id == asset_a.id))
    ).scalar_one()
    assert got_asset.lineage_key == "wk01/lesson_plan"
    assert got_asset.kind == AssetKind.lesson_plan

    got_cv = (
        await db_session.execute(select(ContentVersion).where(ContentVersion.id == cv.id))
    ).scalar_one()
    assert got_cv.seq == 1
    assert got_cv.content == "the body"
    assert got_cv.metadata_ == {"k": "v"}
    assert got_cv.content_hash == ch
    assert got_cv.created_at is not None

    got_cversion = (
        await db_session.execute(
            select(CurriculumVersion).where(CurriculumVersion.id == cversion.id)
        )
    ).scalar_one()
    assert (got_cversion.major, got_cversion.minor, got_cversion.patch) == (1, 0, 0)
    assert got_cversion.parent_version_id is None

    got_member = (
        await db_session.execute(select(VersionMember).where(VersionMember.id == member.id))
    ).scalar_one()
    assert got_member.section == "Week 1"
    assert got_member.asset_version_id == cv.id
    assert got_member.order == 0

    got_edge = (
        await db_session.execute(select(VersionEdge).where(VersionEdge.id == edge.id))
    ).scalar_one()
    assert got_edge.edge_type == "prerequisite"
    assert got_edge.from_asset_id == asset_a.id
    assert got_edge.to_asset_id == asset_b.id
    assert got_edge.validated_against_seq is None


@pytest.mark.asyncio
async def test_content_version_unique_asset_seq(db_session: AsyncSession):
    """(asset_id, seq) is unique — a duplicate seq for the same asset rejects."""
    asset_a, _ = await _seed_two_assets(db_session)
    db_session.add(
        ContentVersion(
            asset_id=asset_a.id, seq=1, content="a", content_hash=content_hash("x", "a", None)
        )
    )
    await db_session.flush()
    db_session.add(
        ContentVersion(
            asset_id=asset_a.id, seq=1, content="b", content_hash=content_hash("x", "b", None)
        )
    )
    with pytest.raises(Exception):  # IntegrityError on the unique constraint
        await db_session.flush()


@pytest.mark.asyncio
async def test_content_version_is_immutable(db_session: AsyncSession):
    """Mutating a persisted ContentVersion must raise (append-only guard)."""
    asset_a, _ = await _seed_two_assets(db_session)
    cv = ContentVersion(
        asset_id=asset_a.id,
        seq=1,
        content="original",
        content_hash=content_hash("lesson_plan", "original", None),
    )
    db_session.add(cv)
    await db_session.flush()

    # Attribute change + flush → the before_update event fires and raises.
    cv.content = "tampered"
    with pytest.raises(ImmutableContentVersionError):
        await db_session.flush()

    # Roll back the poisoned state so the shared fixture stays usable.
    await db_session.rollback()
