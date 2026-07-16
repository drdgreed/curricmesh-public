"""Router: the course-content browser (Feature A).

Three endpoints over the immutable manifest, all addressed by the **legacy
``Asset.id``** contract the frontend navigates by (see ``app/core/course_view.py``):

* ``GET  /api/v1/curricula/{curriculum_id}/calendar`` — the calendar/course view.
* ``GET  /api/v1/assets/{asset_id}``                  — one asset's detail view.
* ``PATCH /api/v1/assets/{asset_id}/source-url``      — set/clear the editable
  source link (guarded by the same write roles as asset mutation).

All endpoints require an authenticated user; the PATCH additionally requires an
editor/author role (mirroring ``app/routers/assets.py``'s ``_WRITE_ROLES``).
Tenant scoping is enforced by the router-level ``tenant_context`` dependency wired
in ``app/main.py`` plus RLS, exactly like the other domain routers.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.core import course_view
from app.database import get_db
from app.models.curriculum import Curriculum
from app.schemas.course import (
    AssetDetailOut,
    AssetEdgeRef,
    AssetVersionRef,
    CalendarSection,
    CalendarTile,
    CourseCalendarOut,
    SourceUrlIn,
    SourceUrlOut,
)

router = APIRouter(prefix="/api/v1", tags=["course"])

# Mirror the asset-mutation write roles (app/routers/assets.py) — the editable
# source link is an author/editor action.
_WRITE_ROLES = require_roles("architect", "instructor", "instructor_lead", "devops")


@router.get(
    "/curricula/{curriculum_id}/calendar", response_model=CourseCalendarOut
)
async def get_course_calendar(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CourseCalendarOut:
    """Return the calendar/course view for the curriculum's active version."""
    curriculum = await db.scalar(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    sections = await course_view.course_calendar(db, curriculum_id)
    return CourseCalendarOut(
        curriculum_id=curriculum_id,
        sections=[
            CalendarSection(
                week_index=s.week_index,
                section=s.section,
                tiles=[
                    CalendarTile(
                        id=t.id,
                        lineage_key=t.lineage_key,
                        kind=t.kind,
                        label=t.label,
                        source_url=t.source_url,
                        latest_version=t.latest_version,
                        status=t.status,
                        misaligned=t.misaligned,
                    )
                    for t in s.tiles
                ],
            )
            for s in sections
        ],
    )


@router.get("/assets/{asset_id}", response_model=AssetDetailOut)
async def get_asset_detail(
    asset_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AssetDetailOut:
    """Return one asset's selected content + history + prerequisites/dependents."""
    detail = await course_view.asset_detail(db, asset_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    return AssetDetailOut(
        id=detail.id,
        lineage_key=detail.lineage_key,
        kind=detail.kind,
        label=detail.label,
        source_url=detail.source_url,
        content=detail.content,
        content_metadata=detail.content_metadata,
        content_seq=detail.content_seq,
        content_hash=detail.content_hash,
        version_history=[
            AssetVersionRef(
                seq=v.seq, content_hash=v.content_hash, created_at=v.created_at
            )
            for v in detail.version_history
        ],
        prerequisites=[
            AssetEdgeRef(
                id=e.id,
                lineage_key=e.lineage_key,
                label=e.label,
                edge_type=e.edge_type,
            )
            for e in detail.prerequisites
        ],
        dependents=[
            AssetEdgeRef(
                id=e.id,
                lineage_key=e.lineage_key,
                label=e.label,
                edge_type=e.edge_type,
            )
            for e in detail.dependents
        ],
    )


@router.patch("/assets/{asset_id}/source-url", response_model=SourceUrlOut)
async def patch_asset_source_url(
    asset_id: uuid.UUID,
    body: SourceUrlIn,
    current: dict[str, Any] = Depends(_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> SourceUrlOut:
    """Set or clear an asset's editable source link, then commit."""
    lineage = await course_view.set_source_url(db, asset_id, body.source_url)
    if lineage is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    await db.commit()
    return SourceUrlOut(
        id=asset_id,
        lineage_key=lineage.lineage_key,
        source_url=lineage.source_url,
    )
