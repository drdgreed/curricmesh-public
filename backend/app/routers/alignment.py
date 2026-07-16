"""Router: GET /api/v1/curricula/{curriculum_id}/alignment

Returns the precise-staleness alignment detail for a curriculum's active version:
one item per stale dependency, carrying *why* it is stale (``mode``) and — when
the edge has captured provenance — exactly how many revisions behind the
dependent is (``revision_delta``). See ``app.core.manifest.manifest_alignment_detail``
and §3.1 of the immutable-version-model design.

Like ``app/routers/graph.py``, the ids in the response are the **legacy Asset
ids** (the API contract the frontend navigates by): each ``LineageAsset`` id is
mapped to its legacy ``Asset.id`` via the shared stable key
(``LineageAsset.lineage_key == Asset.key``), and friendly labels come from
``app.core.naming.asset_display_names``. If the curriculum has no populated
manifest, the item list is empty.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user
from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment_detail,
    version_members,
)
from app.core.naming import asset_display_names
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.structure import Asset
from app.schemas.alignment import AlignmentItem, AlignmentOut

router = APIRouter(prefix="/api/v1/curricula", tags=["alignment"])


@router.get("/{curriculum_id}/alignment", response_model=AlignmentOut)
async def get_curriculum_alignment(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AlignmentOut:
    """Return the active version's stale dependencies (legacy ids + labels)."""
    curriculum = (
        await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    ).scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    cversion = await active_curriculum_version(db, curriculum_id)
    if cversion is None:
        return AlignmentOut(items=[])

    details = await manifest_alignment_detail(db, cversion.id)
    if not details:
        return AlignmentOut(items=[])

    # lineage_id -> lineage_key, for every member of the active version (the
    # endpoints of every staleness detail are always members).
    members = await version_members(db, cversion.id)
    key_by_lineage_id: dict[uuid.UUID, str] = {m.asset_id: m.lineage_key for m in members}

    # Resolve the legacy Assets backing these lineage assets (shared stable key).
    lineage_keys = list(key_by_lineage_id.values())
    legacy_assets = (
        (await db.execute(select(Asset).where(Asset.key.in_(lineage_keys)))).scalars().all()
    )
    asset_by_key: dict[str, Asset] = {a.key: a for a in legacy_assets}
    legacy_id_by_lineage_id: dict[uuid.UUID, uuid.UUID] = {
        lid: asset_by_key[key].id
        for lid, key in key_by_lineage_id.items()
        if key in asset_by_key
    }

    # Friendly labels keyed by legacy Asset id.
    display_by_asset_id = await asset_display_names(db, [a.id for a in legacy_assets])

    def _legacy_id(lineage_id: uuid.UUID) -> uuid.UUID:
        # Fall back to the lineage id when no legacy Asset backs it (an added
        # asset with no legacy row), mirroring graph.py's mapping.
        return legacy_id_by_lineage_id.get(lineage_id, lineage_id)

    def _label(lineage_id: uuid.UUID) -> str:
        legacy_id = legacy_id_by_lineage_id.get(lineage_id)
        if legacy_id is not None and legacy_id in display_by_asset_id:
            return display_by_asset_id[legacy_id]
        return key_by_lineage_id.get(lineage_id, str(lineage_id))

    items = [
        AlignmentItem(
            dependent_id=_legacy_id(d.dependent_asset_id),
            dependent_label=_label(d.dependent_asset_id),
            prerequisite_id=_legacy_id(d.prerequisite_asset_id),
            prerequisite_label=_label(d.prerequisite_asset_id),
            mode=d.mode,
            revision_delta=d.revision_delta,
        )
        for d in details
    ]
    return AlignmentOut(items=items)
