"""Router: watchlist CRUD for the freshness pipeline.

GET  /api/v1/freshness/watchlist              — list watch items (org-scoped automatically via RLS)
POST /api/v1/freshness/watchlist              — create a new watch item (201)
PATCH /api/v1/freshness/watchlist/{item_id}   — partial update (label/institution/url/search_hint/active)

Architect / program_manager only. Simple CRUD — no service layer needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.models.freshness_pipeline import SourceWatchItem

router = APIRouter(prefix="/api/v1/freshness", tags=["freshness"])

_WATCHLIST_ROLES = require_roles("architect", "program_manager")


# ---------------------------------------------------------------------------
# Pydantic models (inline — mirror backend/app/builder/schemas.py style)
# ---------------------------------------------------------------------------


class WatchItemCreate(BaseModel):
    label: str
    institution: str
    url: str
    search_hint: str | None = None
    active: bool = True


class WatchItemUpdate(BaseModel):
    label: str | None = None
    institution: str | None = None
    url: str | None = None
    search_hint: str | None = None
    active: bool | None = None


class WatchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    institution: str
    url: str
    search_hint: str | None
    active: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/watchlist", response_model=list[WatchItemOut])
async def list_watchlist(
    current: dict[str, Any] = Depends(_WATCHLIST_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[WatchItemOut]:
    result = await db.execute(
        select(SourceWatchItem).order_by(SourceWatchItem.created_at)
    )
    return [WatchItemOut.model_validate(item) for item in result.scalars().all()]


@router.post("/watchlist", response_model=WatchItemOut, status_code=201)
async def create_watch_item(
    body: WatchItemCreate,
    current: dict[str, Any] = Depends(_WATCHLIST_ROLES),
    db: AsyncSession = Depends(get_db),
) -> WatchItemOut:
    item = SourceWatchItem(
        label=body.label,
        institution=body.institution,
        url=body.url,
        search_hint=body.search_hint,
        active=body.active,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return WatchItemOut.model_validate(item)


@router.patch("/watchlist/{item_id}", response_model=WatchItemOut)
async def update_watch_item(
    item_id: uuid.UUID,
    body: WatchItemUpdate,
    current: dict[str, Any] = Depends(_WATCHLIST_ROLES),
    db: AsyncSession = Depends(get_db),
) -> WatchItemOut:
    result = await db.execute(
        select(SourceWatchItem).where(SourceWatchItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Watch item not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    return WatchItemOut.model_validate(item)
