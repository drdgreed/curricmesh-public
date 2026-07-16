"""Small query helpers shared across the fork tests (counts, snapshots, content).

Kept deliberately thin — each is one obvious query — so the test bodies read as
assertions about behavior, not plumbing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.manifest import version_edges, version_members
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)


async def _count(session: AsyncSession, model) -> int:
    """Tenant-scoped row count for ``model`` (the read is org-filtered)."""
    return (await session.scalar(select(func.count()).select_from(model))) or 0


async def counts(session: AsyncSession) -> dict[str, int]:
    """A snapshot of the immutable-model table sizes (current tenant)."""
    return {
        "content_versions": await _count(session, ContentVersion),
        "curriculum_versions": await _count(session, CurriculumVersion),
        "version_members": await _count(session, VersionMember),
        "version_edges": await _count(session, VersionEdge),
        "lineage_assets": await _count(session, LineageAsset),
    }


async def members_by_key(session: AsyncSession, version_id: uuid.UUID) -> dict:
    """``{lineage_key: ManifestMember}`` for a version (manifest read layer)."""
    return {m.lineage_key: m for m in await version_members(session, version_id)}


async def edge_key_set(session: AsyncSession, version_id: uuid.UUID) -> set:
    """The version's edges as a set of ``(from_id, to_id, edge_type)`` tuples."""
    return {
        (e.from_asset_id, e.to_asset_id, e.edge_type)
        for e in await version_edges(session, version_id)
    }


async def content_of(session: AsyncSession, content_version_id: uuid.UUID) -> ContentVersion:
    """Fetch a ContentVersion row by id."""
    return await session.scalar(
        select(ContentVersion).where(ContentVersion.id == content_version_id)
    )


async def member_content_rows(session: AsyncSession, version_id: uuid.UUID) -> list:
    """``[(VersionMember, ContentVersion)]`` for a version (joined)."""
    rows = (
        await session.execute(
            select(VersionMember, ContentVersion)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .where(VersionMember.curriculum_version_id == version_id)
        )
    ).all()
    return [(m, c) for m, c in rows]
