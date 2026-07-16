"""Router: GET /api/v1/curricula/{curriculum_id}/versions/{head_id}/diff

Returns the structural delta between two of a curriculum's immutable versions:
assets added / removed / changed (with from→to selected-revision seq + hash) and
edges added / removed. ``base`` is a query param; when omitted it defaults to the
head version's ``parent_version_id`` (and an empty diff when head has no parent —
e.g. the root version).

Ids in the response are the **legacy Asset ids** + friendly labels (same contract
as ``app/routers/graph.py``), resolved via the shared stable key
(``LineageAsset.lineage_key == Asset.key``) and
``app.core.naming.asset_display_names``. Edges are rendered as endpoint labels.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user
from app.core.naming import asset_display_names
from app.core.version_diff import VersionDiff, version_diff
from app.database import get_db
from app.core.manifest import active_curriculum_version, version_members
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.structure import Asset
from app.schemas.version_diff import (
    ActiveVersionOut,
    AssetChangeOut,
    DiffAssetOut,
    DiffEdgeOut,
    VersionDiffOut,
)

router = APIRouter(prefix="/api/v1/curricula", tags=["version-diff"])


async def _version_in_curriculum(
    db: AsyncSession, curriculum_id: uuid.UUID, version_id: uuid.UUID
) -> CurriculumVersion | None:
    return await db.scalar(
        select(CurriculumVersion).where(
            CurriculumVersion.id == version_id,
            CurriculumVersion.curriculum_id == curriculum_id,
        )
    )


@router.get(
    "/{curriculum_id}/active-version",
    response_model=ActiveVersionOut,
)
async def get_active_version(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActiveVersionOut:
    """Resolve the curriculum's active ``CurriculumVersion`` id + its parent.

    The frontend uses this to drive the default "what changed in the current
    version" diff: ``/versions/{head}/diff`` keys on ``CurriculumVersion`` ids,
    which the legacy ``/versions`` list does not surface.
    """
    curriculum = (
        await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    ).scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    cversion = await active_curriculum_version(db, curriculum_id)
    if cversion is None:
        raise HTTPException(
            status_code=404, detail="No active version for this curriculum"
        )

    status = getattr(cversion.status, "value", str(cversion.status))
    return ActiveVersionOut(
        curriculum_id=curriculum_id,
        head_version_id=cversion.id,
        parent_version_id=cversion.parent_version_id,
        semver=f"{cversion.major}.{cversion.minor}.{cversion.patch}",
        status=status,
    )


@router.get(
    "/{curriculum_id}/versions/{head_id}/diff",
    response_model=VersionDiffOut,
)
async def get_version_diff(
    curriculum_id: uuid.UUID,
    head_id: uuid.UUID,
    base: uuid.UUID | None = Query(default=None),
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VersionDiffOut:
    """Diff ``head_id`` against ``base`` (default: head's parent) for a curriculum."""
    curriculum = (
        await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    ).scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    head = await _version_in_curriculum(db, curriculum_id, head_id)
    if head is None:
        raise HTTPException(
            status_code=404, detail="Version not found in this curriculum"
        )

    # Resolve the base: explicit query param, else the head's parent.
    base_id = base if base is not None else head.parent_version_id
    if base_id is None:
        # Root version (no parent, no explicit base) → empty diff.
        return VersionDiffOut(
            base_version_id=None,
            head_version_id=head_id,
            assets_added=[],
            assets_removed=[],
            assets_changed=[],
            edges_added=[],
            edges_removed=[],
        )

    base_version = await _version_in_curriculum(db, curriculum_id, base_id)
    if base_version is None:
        raise HTTPException(
            status_code=404, detail="Base version not found in this curriculum"
        )

    diff = await version_diff(db, base_id, head_id)
    return await _to_out(db, base_id, head_id, diff)


async def _to_out(
    db: AsyncSession,
    base_id: uuid.UUID,
    head_id: uuid.UUID,
    diff: VersionDiff,
) -> VersionDiffOut:
    """Map a core ``VersionDiff`` (lineage ids) to the HTTP shape (legacy ids + labels)."""
    # Build a lineage_id -> lineage_key map across BOTH versions, so removed
    # assets (only in base) and added assets (only in head) both resolve.
    key_by_lineage_id: dict[uuid.UUID, str] = {}
    for cv_id in (base_id, head_id):
        for m in await version_members(db, cv_id):
            key_by_lineage_id[m.asset_id] = m.lineage_key

    lineage_keys = list(set(key_by_lineage_id.values()))
    legacy_assets = (
        (await db.execute(select(Asset).where(Asset.key.in_(lineage_keys)))).scalars().all()
        if lineage_keys
        else []
    )
    asset_by_key: dict[str, Asset] = {a.key: a for a in legacy_assets}
    legacy_id_by_lineage_id: dict[uuid.UUID, uuid.UUID] = {
        lid: asset_by_key[key].id
        for lid, key in key_by_lineage_id.items()
        if key in asset_by_key
    }
    display_by_asset_id = await asset_display_names(db, [a.id for a in legacy_assets])

    def _legacy_id(lineage_id: uuid.UUID) -> uuid.UUID:
        return legacy_id_by_lineage_id.get(lineage_id, lineage_id)

    def _label(lineage_id: uuid.UUID) -> str:
        legacy_id = legacy_id_by_lineage_id.get(lineage_id)
        if legacy_id is not None and legacy_id in display_by_asset_id:
            return display_by_asset_id[legacy_id]
        return key_by_lineage_id.get(lineage_id, str(lineage_id))

    return VersionDiffOut(
        base_version_id=base_id,
        head_version_id=head_id,
        assets_added=[
            DiffAssetOut(
                asset_id=_legacy_id(a.asset_id),
                label=_label(a.asset_id),
                seq=a.seq,
                content_hash=a.content_hash,
            )
            for a in diff.assets_added
        ],
        assets_removed=[
            DiffAssetOut(
                asset_id=_legacy_id(a.asset_id),
                label=_label(a.asset_id),
                seq=a.seq,
                content_hash=a.content_hash,
            )
            for a in diff.assets_removed
        ],
        assets_changed=[
            AssetChangeOut(
                asset_id=_legacy_id(c.asset_id),
                label=_label(c.asset_id),
                from_seq=c.from_seq,
                from_hash=c.from_hash,
                to_seq=c.to_seq,
                to_hash=c.to_hash,
            )
            for c in diff.assets_changed
        ],
        edges_added=[
            DiffEdgeOut(
                from_label=_label(e.from_asset_id),
                to_label=_label(e.to_asset_id),
                edge_type=e.edge_type,
            )
            for e in diff.edges_added
        ],
        edges_removed=[
            DiffEdgeOut(
                from_label=_label(e.from_asset_id),
                to_label=_label(e.to_asset_id),
                edge_type=e.edge_type,
            )
            for e in diff.edges_removed
        ],
    )
