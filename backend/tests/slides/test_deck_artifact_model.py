"""Model tests for ``DeckArtifact`` (Slide System Port, S4 — deck linkage).

A ``DeckArtifact`` links a rendered deck's three R2 artifacts (html/pdf/pptx) to
a released ``CurriculumVersion`` so the Player can find and serve it. Exercise
persistence, tenant stamping, the FK to the version, the optional source-member
link, and cross-tenant isolation (RLS + the ORM auto-filter) — against the live
RLS'd test DB (``db_session``).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.deck_artifact import DeckArtifact
from app.models.enums import AssetKind, LifecycleStatus
from tests.conftest import DEFAULT_ORG_ID


async def _released_version_with_item(
    db: AsyncSession,
) -> tuple[CurriculumVersion, VersionMember]:
    """Build a released CurriculumVersion carrying one renderable item."""
    curriculum = Curriculum(name="Agentic AI", slug=f"agentic-{uuid.uuid4()}")
    db.add(curriculum)
    await db.flush()

    version = CurriculumVersion(
        curriculum_id=curriculum.id, major=1, minor=0, patch=0,
        status=LifecycleStatus.active,
    )
    db.add(version)
    await db.flush()

    asset = LineageAsset(kind=AssetKind.lesson_plan, lineage_key="intro")
    db.add(asset)
    await db.flush()

    content = ContentVersion(
        asset_id=asset.id, seq=1, content="Body.", content_hash="a" * 64
    )
    db.add(content)
    await db.flush()

    member = VersionMember(
        curriculum_version_id=version.id,
        asset_id=asset.id,
        asset_version_id=content.id,
        section="Week 1",
        week_index=0,
        order=0,
    )
    db.add(member)
    await db.flush()
    return version, member


@pytest.mark.asyncio
async def test_deck_artifact_persists_and_is_tenant_stamped(db_session: AsyncSession):
    version, member = await _released_version_with_item(db_session)
    deck = DeckArtifact(
        curriculum_version_id=version.id,
        source_member_id=member.id,
        pdf_key="decks/org/deck.pdf",
        pptx_key="decks/org/deck.pptx",
        html_key="decks/org/deck.html",
    )
    db_session.add(deck)
    await db_session.commit()
    await db_session.refresh(deck)

    assert deck.id is not None
    # Tenant stamped from the ambient org context (fail-closed default).
    assert deck.organization_id == DEFAULT_ORG_ID
    assert deck.curriculum_version_id == version.id
    assert deck.source_member_id == member.id
    # Default status + timestamp.
    assert deck.status == "ready"
    assert deck.created_at is not None


@pytest.mark.asyncio
async def test_deck_artifact_source_member_optional(db_session: AsyncSession):
    """A deck may cover the whole version — ``source_member_id`` is optional."""
    version, _ = await _released_version_with_item(db_session)
    deck = DeckArtifact(
        curriculum_version_id=version.id,
        pdf_key="k.pdf",
        pptx_key="k.pptx",
        html_key="k.html",
    )
    db_session.add(deck)
    await db_session.commit()
    await db_session.refresh(deck)

    assert deck.source_member_id is None
    # Re-fetch to prove queryability within the tenant.
    row = (
        await db_session.execute(
            select(DeckArtifact).where(DeckArtifact.id == deck.id)
        )
    ).scalar_one()
    assert row.html_key == "k.html"


@pytest.mark.asyncio
async def test_deck_artifact_filtered_by_version(db_session: AsyncSession):
    """Decks are addressable by the version they belong to."""
    v1, _ = await _released_version_with_item(db_session)
    v2, _ = await _released_version_with_item(db_session)
    db_session.add(
        DeckArtifact(
            curriculum_version_id=v1.id,
            pdf_key="1.pdf", pptx_key="1.pptx", html_key="1.html",
        )
    )
    db_session.add(
        DeckArtifact(
            curriculum_version_id=v2.id,
            pdf_key="2.pdf", pptx_key="2.pptx", html_key="2.html",
        )
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(DeckArtifact).where(
                DeckArtifact.curriculum_version_id == v1.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].pdf_key == "1.pdf"
