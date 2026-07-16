"""Router: /api/v1/ccrs — Change Control Request submission and listing."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.core.history import EventType
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import submit_ccr
from app.database import get_db
from app.integrations.notifier import notify
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.version import Version
from app.models.workflow import ChangeRequest
from app.schemas.workflow import CCRCreate, CCROut

router = APIRouter(prefix="/api/v1/ccrs", tags=["ccr"])

_SUBMIT_ROLES = require_roles("instructor", "instructor_lead", "architect", "program_manager")

_INSTRUCTOR_OVERRIDE_ROLES = frozenset({"instructor_lead", "architect"})


@router.post("", response_model=CCROut, status_code=201)
async def create_ccr(
    body: CCRCreate,
    current: dict[str, Any] = Depends(_SUBMIT_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CCROut:
    """Submit a new CCR. WorkflowError → 400 via central handler."""
    actor_id: uuid.UUID | None = None
    try:
        actor_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    # Role-gate instructor_override: only instructor_lead or architect may use it
    if body.instructor_override and current["role"] not in _INSTRUCTOR_OVERRIDE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="instructor_override requires instructor_lead or architect",
        )

    # Verify curriculum exists (FK-missing → 404, not 500)
    cur_result = await db.execute(
        select(Curriculum).where(Curriculum.id == body.curriculum_id)
    )
    if cur_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    # Validate target_version_id if provided: must exist and belong to this curriculum
    if body.target_version_id is not None:
        ver_result = await db.execute(
            select(Version).where(
                Version.id == body.target_version_id,
                Version.curriculum_id == body.curriculum_id,
            )
        )
        if ver_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=404,
                detail="target_version_id not found or does not belong to this curriculum",
            )

    affected_kinds: set[AssetKind] = set(body.affected_kinds)

    # proposed_bump is already a BumpType enum (Pydantic validates it)
    bump: BumpType = body.proposed_bump

    # submit_ccr flushes; we commit here (router owns the boundary)
    ccr = await submit_ccr(
        db,
        curriculum_id=body.curriculum_id,
        author_id=actor_id,
        title=body.title,
        rationale=body.rationale,
        proposed_bump=bump,
        affected_kinds=affected_kinds,
        instructor_override=body.instructor_override,
        target_version_id=body.target_version_id,
        affected_asset_ids=body.affected_asset_ids,
        external_link=body.external_link,
    )

    # Persist the structured executable change-set (PR-style review → merge).
    # submit_ccr owns the rule guards / CCR shape; the change-set is pass-through
    # transport, so we set it on the flushed CCR here (JSONB stored as plain dict).
    if body.change_set is not None:
        ccr.change_set = body.change_set.model_dump(mode="json")
        db.add(ccr)

    await db.commit()
    await db.refresh(ccr)

    # Notify AFTER commit — a notification failure must NOT roll back the transaction.
    # NOTE: notify is awaited inline here for simplicity. Production optimization:
    # pass it to FastAPI BackgroundTasks so notification latency is off the request
    # path (i.e. the HTTP response is returned before channels are hit).
    await notify(
        EventType.ccr_created,
        {
            "ccr_id": str(ccr.id),
            "title": ccr.title,
            "curriculum_id": str(ccr.curriculum_id),
            "proposed_bump": ccr.proposed_bump or "",
        },
    )

    return CCROut.model_validate(ccr)


@router.get("", response_model=list[CCROut])
async def list_ccrs(
    status: str | None = Query(default=None),
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CCROut]:
    stmt = select(ChangeRequest).order_by(ChangeRequest.created_at)
    if status is not None:
        try:
            status_enum = LifecycleStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status filter: {status!r}")
        stmt = stmt.where(ChangeRequest.status == status_enum)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [CCROut.model_validate(r) for r in rows]
