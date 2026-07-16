"""Router: /api/v1/curricula — CRUD for Curriculum objects."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.database import get_db
from app.models.curriculum import Curriculum
from app.schemas.curricula import CurriculumCreate, CurriculumOut

router = APIRouter(prefix="/api/v1/curricula", tags=["curricula"])

_WRITE_ROLES = require_roles("architect", "program_manager")


@router.post("", response_model=CurriculumOut, status_code=201)
async def create_curriculum(
    body: CurriculumCreate,
    current: dict[str, Any] = Depends(_WRITE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CurriculumOut:
    curriculum = Curriculum(name=body.name, slug=body.slug)
    db.add(curriculum)
    await db.commit()
    await db.refresh(curriculum)
    return CurriculumOut.model_validate(curriculum)


@router.get("", response_model=list[CurriculumOut])
async def list_curricula(
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CurriculumOut]:
    result = await db.execute(select(Curriculum).order_by(Curriculum.created_at))
    rows = result.scalars().all()
    return [CurriculumOut.model_validate(r) for r in rows]


@router.get("/{curriculum_id}", response_model=CurriculumOut)
async def get_curriculum(
    curriculum_id: str,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurriculumOut:
    result = await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    curriculum = result.scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return CurriculumOut.model_validate(curriculum)
