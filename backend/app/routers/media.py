"""Router: owned-media asset upload (presigned direct-to-storage) + confirm +
list + presigned serve + delete.

POST   /api/v1/media/upload-url      — generate a presigned PUT URL + create a
                                       pending MediaAsset row.
POST   /api/v1/media/{id}/confirm    — mark the asset ready after the client PUT.
GET    /api/v1/media                 — list the caller-org's assets.
GET    /api/v1/media/{id}            — return asset + presigned download_url.
DELETE /api/v1/media/{id}            — remove asset row + storage object (204).

Reads (list + get) — instructor / instructor_lead / architect / program_manager
(the author tier, so the media picker populates for anyone who can attach media).
Writes (upload-url / confirm / delete) — architect / program_manager only.
Storage disabled (STORAGE_BUCKET unset) → 503, handled by the get_storage()
dependency before the endpoint body runs.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.media.storage import StorageBackend, get_storage
from app.models.media import MediaAsset
from app.tenant import require_org

router = APIRouter(prefix="/api/v1/media", tags=["media"])

_MEDIA_ROLES = require_roles("architect", "program_manager")
# Reads (list + get) are open to the full author tier so instructors who can
# attach media to draft items (builder router) can also browse/preview the
# shared media library in the picker. Writes (upload/confirm/delete) stay
# manager-tier — library management is architect/program_manager.
_MEDIA_READ_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)

logger = logging.getLogger(__name__)

# Reusable kind type so the Literal stays DRY across request models + filters.
_MediaKind = Literal["video", "audio", "image", "pdf", "doc", "other"]
_MediaStatus = Literal["pending", "ready", "failed"]


# ---------------------------------------------------------------------------
# Pydantic models (inline — mirror freshness_watchlist.py style)
# ---------------------------------------------------------------------------


class UploadUrlRequest(BaseModel):
    filename: str = Field(max_length=512)
    mime: str = Field(max_length=255)
    kind: _MediaKind


class UploadUrlResponse(BaseModel):
    asset_id: uuid.UUID
    upload_url: str
    storage_key: str


class ConfirmRequest(BaseModel):
    checksum: str = Field(min_length=1, max_length=64)  # sha256 hex fits column
    duration_s: float | None = None


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    filename: str
    mime: str
    size_bytes: int | None
    checksum: str | None
    duration_s: float | None
    status: str
    storage_key: str
    created_at: datetime


class MediaAssetDetail(MediaAssetOut):
    """Extends MediaAssetOut with a fresh presigned download_url.

    ``download_url`` is populated only when ``status == "ready"``; it is
    ``None`` for pending / failed assets (there is nothing to download yet).
    """

    download_url: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _safe_filename(filename: str) -> str:
    """Strip path separators and return the basename only.

    Prevents a crafted filename like ``../../evil`` from escaping the
    org-scoped prefix in the storage key.
    """
    # Normalise both Unix and Windows separators so basename works on both.
    name = os.path.basename(filename.replace("\\", "/"))
    return name if name else "upload"


@router.post("/upload-url", response_model=UploadUrlResponse, status_code=201)
async def upload_url(
    body: UploadUrlRequest,
    current: dict[str, Any] = Depends(_MEDIA_ROLES),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> UploadUrlResponse:
    """Generate a presigned PUT URL and create a pending MediaAsset row.

    The client PUTs directly to the returned URL; the backend never proxies
    bytes.  Call ``POST /{id}/confirm`` after the upload completes.
    """
    org_id = require_org()
    safe_name = _safe_filename(body.filename)
    storage_key = f"{org_id}/media/{uuid.uuid4()}/{safe_name}"

    # sub is the user UUID (set by create_access_token / JWT).
    created_by: uuid.UUID | None = None
    try:
        created_by = uuid.UUID(current["sub"])
    except (KeyError, ValueError, TypeError):
        pass  # created_by is nullable — tolerate tokens without a valid sub

    asset = MediaAsset(
        kind=body.kind,
        filename=body.filename,
        mime=body.mime,
        storage_key=storage_key,
        status="pending",
        created_by=created_by,
    )
    db.add(asset)
    await db.flush()
    await db.commit()
    await db.refresh(asset)

    presigned = storage.presigned_put_url(storage_key, body.mime)

    return UploadUrlResponse(
        asset_id=asset.id,
        upload_url=presigned,
        storage_key=storage_key,
    )


@router.post("/{asset_id}/confirm", response_model=MediaAssetOut)
async def confirm_upload(
    asset_id: uuid.UUID,
    body: ConfirmRequest,
    current: dict[str, Any] = Depends(_MEDIA_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> MediaAssetOut:
    """Confirm the client has finished uploading an object.

    Verifies the object exists in storage (HEAD), records size + checksum,
    and transitions the asset to ``ready``.  Returns 400 if the object is not
    yet present (client skipped the PUT), 404 if the asset row is not found
    in the caller's org (cross-org assets are invisible via the ORM
    tenant-scope filter).
    """
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    head = storage.head(asset.storage_key)
    if head is None:
        raise HTTPException(status_code=400, detail="object not uploaded")

    asset.size_bytes = head["size"]
    asset.checksum = body.checksum
    if body.duration_s is not None:
        asset.duration_s = body.duration_s
    asset.status = "ready"

    await db.commit()
    await db.refresh(asset)
    return MediaAssetOut.model_validate(asset)


# ---------------------------------------------------------------------------
# Task 4 endpoints: list, presigned get, delete
# ---------------------------------------------------------------------------


@router.get("", response_model=list[MediaAssetOut])
async def list_media(
    status: _MediaStatus | None = Query(default=None),
    kind: _MediaKind | None = Query(default=None),
    current: dict[str, Any] = Depends(_MEDIA_READ_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),  # noqa: ARG001 — ensures 503 when disabled
) -> list[MediaAssetOut]:
    """List the caller-org's media assets, newest first.

    Optional filters:
    - ``?status=pending|ready|failed``
    - ``?kind=video|audio|image|pdf|doc|other``
    """
    stmt = select(MediaAsset).order_by(MediaAsset.created_at.desc())
    if status is not None:
        stmt = stmt.where(MediaAsset.status == status)
    if kind is not None:
        stmt = stmt.where(MediaAsset.kind == kind)
    result = await db.execute(stmt)
    return [MediaAssetOut.model_validate(a) for a in result.scalars().all()]


@router.get("/{asset_id}", response_model=MediaAssetDetail)
async def get_media(
    asset_id: uuid.UUID,
    current: dict[str, Any] = Depends(_MEDIA_READ_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> MediaAssetDetail:
    """Return an asset with a fresh presigned download URL.

    ``download_url`` is populated only when ``status == "ready"``; it is
    ``None`` for pending / failed assets.  404 if the asset does not exist
    in the caller's org (tenant-scope filter makes cross-org assets invisible).
    """
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    download_url: str | None = None
    if asset.status == "ready":
        download_url = storage.presigned_get_url(asset.storage_key)

    return MediaAssetDetail(
        **MediaAssetOut.model_validate(asset).model_dump(),
        download_url=download_url,
    )


@router.delete("/{asset_id}", status_code=204)
async def delete_media(
    asset_id: uuid.UUID,
    current: dict[str, Any] = Depends(_MEDIA_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> None:
    """Delete a media asset row and its backing storage object.

    Storage deletion is best-effort: if it fails (e.g. the object was already
    gone), we log a warning but continue with the row delete so the DB stays
    consistent.

    404 if the asset is not found in the caller's org.

    # TODO(slice-2): guard against deleting an asset referenced by a published
    # course (no reference table yet).
    """
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    try:
        storage.delete(asset.storage_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "storage.delete failed for key %s (asset %s); proceeding with row delete: %s",
            asset.storage_key,
            asset_id,
            exc,
        )

    await db.delete(asset)
    await db.commit()
