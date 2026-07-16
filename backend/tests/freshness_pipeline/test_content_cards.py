"""Tests for ``app.freshness_pipeline.content_cards.build_content_cards``.

Seeding pattern mirrors ``tests/integration/test_content_model.py``:
uses the ``db_session`` fixture (DEFAULT_ORG, live RLS) and inserts rows
directly into the 5-table immutable model without the full seed+backfill
round-trip.  The ``active_content_version_id`` pointer on the ``Curriculum``
row is what ``build_content_cards`` checks first.

Test cases
----------
1. no_active_version       — active_content_version_id is None → returns None
2. two_ordered_members     — two members with markdown content → two cards,
                             correct excerpt / headings / word_count / first_line,
                             ordered by (week_index, order), kind emitted as str
3. empty_content_body      — member with body=="" → card with empty excerpt,
                             empty headings, word_count 0, no crash
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.freshness_pipeline.content_cards import build_content_cards
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_curriculum(session: AsyncSession, *, active_cv_id: uuid.UUID | None = None) -> Curriculum:
    """Insert a Curriculum row, optionally with active_content_version_id set."""
    cur = Curriculum(name="Test Curriculum", slug=f"tc-{uuid.uuid4().hex[:8]}")
    cur.active_content_version_id = active_cv_id
    session.add(cur)
    await session.flush()
    return cur


async def _make_lineage(
    session: AsyncSession, *, key: str, kind: AssetKind = AssetKind.lesson_plan
) -> LineageAsset:
    la = LineageAsset(kind=kind, lineage_key=key)
    session.add(la)
    await session.flush()
    return la


async def _make_content_version(
    session: AsyncSession,
    *,
    asset: LineageAsset,
    seq: int,
    body: str,
) -> ContentVersion:
    ch = content_hash(asset.kind.value, body, None)
    cv = ContentVersion(
        asset_id=asset.id,
        seq=seq,
        content=body,
        metadata_=None,
        content_hash=ch,
    )
    session.add(cv)
    await session.flush()
    return cv


async def _make_curriculum_version(
    session: AsyncSession, *, curriculum_id: uuid.UUID
) -> CurriculumVersion:
    cv = CurriculumVersion(
        curriculum_id=curriculum_id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
        parent_version_id=None,
    )
    session.add(cv)
    await session.flush()
    return cv


async def _make_member(
    session: AsyncSession,
    *,
    curriculum_version_id: uuid.UUID,
    asset: LineageAsset,
    content_version: ContentVersion,
    section: str,
    week_index: int,
    order: int,
) -> VersionMember:
    vm = VersionMember(
        curriculum_version_id=curriculum_version_id,
        asset_id=asset.id,
        asset_version_id=content_version.id,
        section=section,
        week_index=week_index,
        order=order,
    )
    session.add(vm)
    await session.flush()
    return vm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_active_version_returns_none(db_session: AsyncSession):
    """active_content_version_id is None → build_content_cards returns None."""
    cur = await _make_curriculum(db_session, active_cv_id=None)
    result = await build_content_cards(db_session, cur)
    assert result is None


@pytest.mark.asyncio
async def test_two_ordered_members_correct_cards(db_session: AsyncSession):
    """Two members with markdown content → two ordered cards with correct fields."""
    # Week 2 member is inserted first to test that ordering is by week_index, not
    # insertion order.
    cur = await _make_curriculum(db_session)

    cv_version = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    # Member B: week 2 (inserted first)
    asset_b = await _make_lineage(db_session, key="wk02/lesson_plan", kind=AssetKind.lesson_plan)
    body_b = "# Week 2\n\n## Introduction\nSome intro text.\n\n## Summary\nEnd matter."
    content_b = await _make_content_version(db_session, asset=asset_b, seq=1, body=body_b)
    await _make_member(
        db_session,
        curriculum_version_id=cv_version.id,
        asset=asset_b,
        content_version=content_b,
        section="Week 2",
        week_index=2,
        order=0,
    )

    # Member A: week 1 (inserted second)
    asset_a = await _make_lineage(db_session, key="wk01/lesson_plan", kind=AssetKind.lesson_plan)
    body_a = "# Week 1\n\n## Core Concepts\nFirst heading content.\n\nSome body text here."
    content_a = await _make_content_version(db_session, asset=asset_a, seq=1, body=body_a)
    await _make_member(
        db_session,
        curriculum_version_id=cv_version.id,
        asset=asset_a,
        content_version=content_a,
        section="Week 1",
        week_index=1,
        order=0,
    )

    # Set the active pointer AFTER we have the version id.
    cur.active_content_version_id = cv_version.id
    await db_session.flush()

    cards = await build_content_cards(db_session, cur)

    assert cards is not None
    assert len(cards) == 2

    # Ordered by (week_index, order): Week 1 first.
    c1, c2 = cards

    # --- Card 1 (Week 1) ---
    assert c1["lineage_key"] == "wk01/lesson_plan"
    assert c1["kind"] == "lesson_plan"          # enum .value, not enum instance
    assert isinstance(c1["kind"], str)
    assert c1["section"] == "Week 1"
    assert c1["week_index"] == 1
    assert c1["first_line"] == "# Week 1"
    # excerpt = first 400 chars of whitespace-collapsed body
    collapsed_a = " ".join(body_a.split())
    assert c1["excerpt"] == collapsed_a[:400]
    assert c1["headings"] == ["Core Concepts"]
    assert c1["word_count"] == len(body_a.split())

    # --- Card 2 (Week 2) ---
    assert c2["lineage_key"] == "wk02/lesson_plan"
    assert c2["week_index"] == 2
    assert c2["headings"] == ["Introduction", "Summary"]
    assert c2["word_count"] == len(body_b.split())
    collapsed_b = " ".join(body_b.split())
    assert c2["excerpt"] == collapsed_b[:400]


@pytest.mark.asyncio
async def test_empty_content_body_produces_zero_card(db_session: AsyncSession):
    """A member with body="" yields a card: empty excerpt, empty headings, word_count 0."""
    cur = await _make_curriculum(db_session)
    cv_version = await _make_curriculum_version(db_session, curriculum_id=cur.id)

    asset = await _make_lineage(db_session, key="wk01/empty", kind=AssetKind.assessment)
    content = await _make_content_version(db_session, asset=asset, seq=1, body="")
    await _make_member(
        db_session,
        curriculum_version_id=cv_version.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )

    cur.active_content_version_id = cv_version.id
    await db_session.flush()

    cards = await build_content_cards(db_session, cur)

    assert cards is not None
    assert len(cards) == 1
    card = cards[0]
    assert card["excerpt"] == ""
    assert card["headings"] == []
    assert card["word_count"] == 0
    assert card["first_line"] == ""
    assert card["kind"] == "assessment"
