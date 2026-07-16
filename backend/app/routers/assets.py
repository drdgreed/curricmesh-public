"""Router: /api/v1/assets — Asset and AssetVersion CRUD."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.database import get_db
from app.models.structure import Asset, AssetVersion
from app.schemas.assets import AssetCreate, AssetOut, AssetVersionCreate, AssetVersionListItem, AssetVersionOut

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

_WRITE_ROLES = require_roles("architect", "instructor", "instructor_lead", "devops")


@router.get("", response_model=list[AssetOut])
async def list_assets(
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AssetOut]:
    result = await db.execute(select(Asset).order_by(Asset.created_at))
    rows = result.scalars().all()
    return [AssetOut.model_validate(r) for r in rows]


@router.post("", response_model=AssetOut, status_code=201)
async def create_asset(
    body: AssetCreate,
    current: dict[str, Any] = Depends(_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> AssetOut:
    asset = Asset(
        kind=body.kind,
        key=body.key,
        module_id=body.module_id,
        project_id=body.project_id,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return AssetOut.model_validate(asset)


@router.get("/{asset_id}/versions", response_model=list[AssetVersionListItem])
async def list_asset_versions(
    asset_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AssetVersionListItem]:
    """List all versions for an asset, newest first.

    Returns 404 if the asset does not exist.
    """
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    rows_result = await db.execute(
        select(AssetVersion)
        .where(AssetVersion.asset_id == asset_id)
        .order_by(
            AssetVersion.major.desc(),
            AssetVersion.minor.desc(),
            AssetVersion.patch.desc(),
        )
    )
    versions = rows_result.scalars().all()
    return [
        AssetVersionListItem(
            id=av.id,
            semver=f"{av.major}.{av.minor}.{av.patch}",
            status=av.status.value if hasattr(av.status, "value") else str(av.status),
            created_at=av.created_at,
        )
        for av in versions
    ]


@router.post("/{asset_id}/versions", response_model=AssetVersionOut, status_code=201)
async def create_asset_version(
    asset_id: uuid.UUID,
    body: AssetVersionCreate,
    current: dict[str, Any] = Depends(_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> AssetVersionOut:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    av = AssetVersion(
        asset_id=asset_id,
        major=body.major,
        minor=body.minor,
        patch=body.patch,
        body_ref=body.body_ref,
        metadata_=body.metadata_,
    )
    db.add(av)
    await db.commit()
    await db.refresh(av)
    return AssetVersionOut.model_validate(av)
