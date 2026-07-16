"""Routers for student-portfolio version-pinning (V3-B).

Thin CRUD over ``version_pins``: create/list/filter/unpin a per-student pin and
list pins for a curriculum. Tenant isolation is provided by the app-layer
auto-filter (and Postgres RLS under a least-privilege role) — the endpoints
never filter on ``organization_id`` themselves.

Two routers share this module because the resource lives under two prefixes:
``/api/v1/pins`` (the collection) and ``/api/v1/curricula/{id}/pins`` (the
curriculum sub-resource). Both are registered under the ``tenant_context`` group.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.models.version import Version
from app.models.version_pin import VersionPin
from app.schemas.pin import PinCreate, PinOut

router = APIRouter(prefix="/api/v1/pins", tags=["pins"])
curriculum_router = APIRouter(prefix="/api/v1/curricula", tags=["pins"])

# Instructors enroll students, so they can pin alongside architects/PMs.
_PIN_ROLES = require_roles("program_manager", "architect", "instructor")
_UNPIN_ROLES = require_roles("program_manager", "architect")


@router.post("", response_model=PinOut, status_code=201)
async def create_pin(
    body: PinCreate,
    current: dict[str, Any] = Depends(_PIN_ROLES),
    db: AsyncSession = Depends(get_db),
) -> PinOut:
    """Pin a student to the exact version they trained on.

    Validates that ``version_id`` belongs to ``curriculum_id`` (both reads are
    org-scoped by the auto-filter, so a cross-tenant id resolves to 404).
    """
    result = await db.execute(
        select(Version).where(
            Version.id == body.version_id,
            Version.curriculum_id == body.curriculum_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404,
            detail="Version not found for this curriculum",
        )

    pin = VersionPin(
        curriculum_id=body.curriculum_id,
        version_id=body.version_id,
        cohort_id=body.cohort_id,
        student_label=body.student_label,
        student_email=body.student_email,
        status=body.status,
    )
    db.add(pin)
    await db.commit()
    await db.refresh(pin)
    return PinOut.model_validate(pin)


@router.get("", response_model=list[PinOut])
async def list_pins(
    curriculum_id: uuid.UUID | None = Query(default=None),
    student_email: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current: dict[str, Any] = Depends(_PIN_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[PinOut]:
    """List pins for the caller's org, optionally filtered.

    Gated to the pin roles: pins carry ``student_email`` (PII), so only roles
    that manage enrollments may enumerate them.
    """
    stmt = select(VersionPin)
    if curriculum_id is not None:
        stmt = stmt.where(VersionPin.curriculum_id == curriculum_id)
    if student_email is not None:
        stmt = stmt.where(VersionPin.student_email == student_email)
    if status is not None:
        stmt = stmt.where(VersionPin.status == status)
    stmt = stmt.order_by(VersionPin.pinned_at)

    result = await db.execute(stmt)
    return [PinOut.model_validate(r) for r in result.scalars().all()]


@router.delete("/{pin_id}", status_code=204)
async def delete_pin(
    pin_id: uuid.UUID,
    current: dict[str, Any] = Depends(_UNPIN_ROLES),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Unpin a student. A cross-tenant id is invisible (404, never 403)."""
    result = await db.execute(select(VersionPin).where(VersionPin.id == pin_id))
    pin = result.scalar_one_or_none()
    if pin is None:
        raise HTTPException(status_code=404, detail="Pin not found")
    await db.delete(pin)
    await db.commit()


@curriculum_router.get("/{curriculum_id}/pins", response_model=list[PinOut])
async def list_curriculum_pins(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(_PIN_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[PinOut]:
    """List all pins for a curriculum (org-scoped, pin-roles only — PII)."""
    result = await db.execute(
        select(VersionPin)
        .where(VersionPin.curriculum_id == curriculum_id)
        .order_by(VersionPin.pinned_at)
    )
    return [PinOut.model_validate(r) for r in result.scalars().all()]
