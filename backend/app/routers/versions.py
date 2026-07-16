"""Router: /api/v1/curricula/{id}/versions and /api/v1/versions/{id}/transition."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.core.history import persist_event
from app.core.versioning.lifecycle import transition
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.version import Version
from app.schemas.curricula import TransitionRequest, VersionCreate, VersionOut

router = APIRouter(tags=["versions"])

_ARCHITECT = require_roles("architect")


@router.post("/api/v1/curricula/{curriculum_id}/versions", response_model=VersionOut, status_code=201)
async def create_version(
    curriculum_id: uuid.UUID,
    body: VersionCreate,
    current: dict[str, Any] = Depends(_ARCHITECT),
    db: AsyncSession = Depends(get_db),
) -> VersionOut:
    # Verify the curriculum exists
    result = await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    version = Version(
        curriculum_id=curriculum_id,
        major=body.major,
        minor=body.minor,
        patch=body.patch,
        notes=body.notes,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return VersionOut.model_validate(version)


@router.get("/api/v1/curricula/{curriculum_id}/versions", response_model=list[VersionOut])
async def list_versions(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VersionOut]:
    # 404 if the curriculum doesn't exist (instead of returning [])
    cur_result = await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    if cur_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    result = await db.execute(
        select(Version)
        .where(Version.curriculum_id == curriculum_id)
        .order_by(Version.created_at)
    )
    rows = result.scalars().all()
    return [VersionOut.model_validate(r) for r in rows]


@router.post("/api/v1/versions/{version_id}/transition", response_model=VersionOut)
async def transition_version(
    version_id: uuid.UUID,
    body: TransitionRequest,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VersionOut:
    """Transition a version to a new lifecycle status.

    Domain exceptions (IllegalTransition, PermissionDenied) are handled by
    the central exception handlers in api_errors.py → 409 / 403.
    """
    result = await db.execute(select(Version).where(Version.id == version_id))
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    actor_id: uuid.UUID | None = None
    try:
        actor_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    # Raises IllegalTransition or PermissionDenied — handled centrally
    updated_version, event = transition(
        version,
        body.to_status,
        actor_role=current["role"],
        actor_id=actor_id,
    )

    await persist_event(db, event)
    await db.commit()
    await db.refresh(updated_version)
    return VersionOut.model_validate(updated_version)
