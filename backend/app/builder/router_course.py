"""Router: /api/v1/builder — DraftCourse + DraftObjective CRUD (Task 2).

The mutable authoring surface of the Course Builder. Unlike the immutable
release path, draft rows are freely mutated in place. Every write builds its
response in-transaction (flush + refresh BEFORE commit), mirroring
``app/routers/releases.py``. Tenant isolation is handled by ``TenantScoped``
loader criteria + the ``app.current_org`` GUC, so the queries here never filter
on ``organization_id`` explicitly — a draft created under org A is invisible to
a session pinned to org B.

``learner_profile`` / ``effort_config`` are stored verbatim as ``model_dump()``
JSONB dicts. ``key_skills`` is stored as a ``{"skills": [...]}`` JSONB envelope
on the model and round-trips back to a flat ``list[str]`` on the wire.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.builder.categorize import extract_metrics, guess_kind
from app.builder.graph_utils import would_create_cycle
from app.builder.models import (
    DraftCourse,
    DraftDependency,
    DraftItem,
    DraftItemMedia,
    DraftItemObjective,
    DraftObjective,
)
from app.builder.schemas import (
    AlignmentCreate,
    AttachMediaRequest,
    CourseCreate,
    CourseOut,
    CourseUpdate,
    DependencyCreate,
    DependencyOut,
    ItemCreate,
    ItemMediaOut,
    ItemOut,
    ItemUpdate,
    ObjectiveCreate,
    ObjectiveOut,
    ObjectiveUpdate,
)
from app.database import get_db
from app.models.media import MediaAsset

router = APIRouter(prefix="/api/v1/builder", tags=["builder"])

# Mirror app/routers/ccr.py::_SUBMIT_ROLES — the author tier.
_AUTHOR_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)


def _objective_out(obj: DraftObjective) -> ObjectiveOut:
    """Build an ObjectiveOut, unwrapping the ``{"skills": [...]}`` envelope."""
    skills = (obj.key_skills or {}).get("skills", []) if obj.key_skills else []
    return ObjectiveOut(
        id=obj.id,
        draft_course_id=obj.draft_course_id,
        text=obj.text,
        bloom_level=obj.bloom_level,
        key_skills=list(skills),
        week_index=obj.week_index,
        order_index=obj.order_index,
    )


async def _get_course(db: AsyncSession, course_id: uuid.UUID) -> DraftCourse:
    """Load a draft course (tenant-scoped) or raise 404."""
    course = (
        await db.execute(select(DraftCourse).where(DraftCourse.id == course_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Draft course not found")
    return course


async def _get_item(db: AsyncSession, item_id: uuid.UUID) -> DraftItem:
    """Load a draft item (tenant-scoped) or raise 404."""
    item = (
        await db.execute(select(DraftItem).where(DraftItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Draft item not found")
    return item


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------


@router.post("/courses", response_model=CourseOut, status_code=201)
async def create_course(
    body: CourseCreate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CourseOut:
    created_by: uuid.UUID | None = None
    try:
        created_by = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    course = DraftCourse(
        title=body.title,
        description=body.description,
        learner_profile=(
            body.learner_profile.model_dump() if body.learner_profile else None
        ),
        effort_config=(
            body.effort_config.model_dump() if body.effort_config else None
        ),
        target_weeks=body.target_weeks,
        status="drafting",
        created_by=created_by,
    )
    db.add(course)
    await db.flush()
    await db.refresh(course)
    out = CourseOut.model_validate(course)
    await db.commit()
    return out


@router.get("/courses", response_model=list[CourseOut])
async def list_courses(
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[CourseOut]:
    rows = (
        await db.execute(
            select(DraftCourse).order_by(DraftCourse.created_at)
        )
    ).scalars().all()
    return [CourseOut.model_validate(r) for r in rows]


@router.get("/courses/{course_id}", response_model=CourseOut)
async def get_course(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CourseOut:
    course = await _get_course(db, course_id)
    return CourseOut.model_validate(course)


@router.patch("/courses/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: uuid.UUID,
    body: CourseUpdate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> CourseOut:
    course = await _get_course(db, course_id)

    fields = body.model_dump(exclude_unset=True)
    if "title" in fields:
        course.title = body.title
    if "description" in fields:
        course.description = body.description
    if "learner_profile" in fields:
        course.learner_profile = (
            body.learner_profile.model_dump() if body.learner_profile else None
        )
    if "effort_config" in fields:
        course.effort_config = (
            body.effort_config.model_dump() if body.effort_config else None
        )
    if "target_weeks" in fields:
        course.target_weeks = body.target_weeks
    if "status" in fields and body.status is not None:
        course.status = body.status

    db.add(course)
    await db.flush()
    await db.refresh(course)
    out = CourseOut.model_validate(course)
    await db.commit()
    return out


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a draft course and all its children.

    All child rows (objectives, items, dependencies, rubric results, advisor
    notes, item-objective alignments, item-media links) are removed by
    database-level CASCADE — no explicit child deletion is required. The
    generation_jobs.course_id FK uses SET NULL, so job history is preserved.

    Returns 204 on success. Cross-org access and unknown ids both return 404.
    """
    course = await _get_course(db, course_id)
    await db.delete(course)
    await db.commit()


# ---------------------------------------------------------------------------
# Objectives
# ---------------------------------------------------------------------------


@router.post(
    "/courses/{course_id}/objectives",
    response_model=ObjectiveOut,
    status_code=201,
)
async def create_objective(
    course_id: uuid.UUID,
    body: ObjectiveCreate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ObjectiveOut:
    await _get_course(db, course_id)  # 404 if missing

    obj = DraftObjective(
        draft_course_id=course_id,
        text=body.text,
        bloom_level=body.bloom_level,
        key_skills={"skills": list(body.key_skills)},
        week_index=body.week_index,
        order_index=body.order_index,
    )
    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    out = _objective_out(obj)
    await db.commit()
    return out


@router.get(
    "/courses/{course_id}/objectives",
    response_model=list[ObjectiveOut],
)
async def list_objectives(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[ObjectiveOut]:
    await _get_course(db, course_id)  # 404 if missing
    rows = (
        await db.execute(
            select(DraftObjective)
            .where(DraftObjective.draft_course_id == course_id)
            .order_by(
                DraftObjective.week_index.nulls_last(),
                DraftObjective.order_index,
            )
        )
    ).scalars().all()
    return [_objective_out(o) for o in rows]


@router.patch("/objectives/{objective_id}", response_model=ObjectiveOut)
async def update_objective(
    objective_id: uuid.UUID,
    body: ObjectiveUpdate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ObjectiveOut:
    obj = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.id == objective_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Draft objective not found")

    fields = body.model_dump(exclude_unset=True)
    if "text" in fields and body.text is not None:
        obj.text = body.text
    if "bloom_level" in fields and body.bloom_level is not None:
        obj.bloom_level = body.bloom_level
    if "key_skills" in fields and body.key_skills is not None:
        obj.key_skills = {"skills": list(body.key_skills)}
    if "week_index" in fields:
        obj.week_index = body.week_index
    if "order_index" in fields and body.order_index is not None:
        obj.order_index = body.order_index

    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    out = _objective_out(obj)
    await db.commit()
    return out


@router.delete("/objectives/{objective_id}", status_code=204)
async def delete_objective(
    objective_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> None:
    obj = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.id == objective_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Draft objective not found")
    await db.delete(obj)
    await db.commit()


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@router.post(
    "/courses/{course_id}/items",
    response_model=ItemOut,
    status_code=201,
)
async def create_item(
    course_id: uuid.UUID,
    body: ItemCreate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ItemOut:
    await _get_course(db, course_id)  # 404 if missing

    # Author-provided values win; fall back to the rule-based heuristics.
    kind = body.kind if body.kind is not None else guess_kind(body.title, body.content)
    metrics = (
        body.metrics
        if body.metrics is not None
        else extract_metrics(body.content, body.source_url)
    )

    item = DraftItem(
        draft_course_id=course_id,
        kind=kind,
        title=body.title,
        content=body.content,
        source_url=body.source_url,
        metrics=metrics or None,
        week_index=body.week_index,
        order_index=body.order_index,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    out = ItemOut.model_validate(item)
    await db.commit()
    return out


@router.get(
    "/courses/{course_id}/items",
    response_model=list[ItemOut],
)
async def list_items(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[ItemOut]:
    await _get_course(db, course_id)  # 404 if missing
    rows = (
        await db.execute(
            select(DraftItem)
            .where(DraftItem.draft_course_id == course_id)
            .order_by(
                DraftItem.week_index.nulls_last(),
                DraftItem.order_index,
            )
        )
    ).scalars().all()
    return [ItemOut.model_validate(r) for r in rows]


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(
    item_id: uuid.UUID,
    body: ItemUpdate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ItemOut:
    item = await _get_item(db, item_id)

    fields = body.model_dump(exclude_unset=True)
    if "title" in fields and body.title is not None:
        item.title = body.title
    if "kind" in fields and body.kind is not None:
        item.kind = body.kind
    if "content" in fields:
        item.content = body.content
    if "source_url" in fields:
        item.source_url = body.source_url
    if "metrics" in fields:
        item.metrics = body.metrics
    if "week_index" in fields:
        item.week_index = body.week_index
    if "order_index" in fields and body.order_index is not None:
        item.order_index = body.order_index
    if "estimated_minutes" in fields:
        item.estimated_minutes = body.estimated_minutes

    db.add(item)
    await db.flush()
    await db.refresh(item)
    out = ItemOut.model_validate(item)
    await db.commit()
    return out


@router.post("/items/{item_id}/objectives", status_code=201)
async def align_item(
    item_id: uuid.UUID,
    body: AlignmentCreate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> dict[str, uuid.UUID]:
    item = await _get_item(db, item_id)

    obj = (
        await db.execute(
            select(DraftObjective).where(DraftObjective.id == body.objective_id)
        )
    ).scalar_one_or_none()
    # Objective must exist AND belong to the same course as the item.
    if obj is None or obj.draft_course_id != item.draft_course_id:
        raise HTTPException(
            status_code=404, detail="Draft objective not found in this course"
        )

    # Idempotent: if the (item, objective) pair already exists, return it rather
    # than tripping the unique constraint.
    existing = (
        await db.execute(
            select(DraftItemObjective).where(
                DraftItemObjective.draft_item_id == item_id,
                DraftItemObjective.draft_objective_id == body.objective_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        link = DraftItemObjective(
            draft_item_id=item_id,
            draft_objective_id=body.objective_id,
        )
        db.add(link)
        await db.flush()
        await db.commit()

    return {"item_id": item_id, "objective_id": body.objective_id}


@router.get("/items/{item_id}/objectives", response_model=list[uuid.UUID])
async def list_item_objectives(
    item_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[uuid.UUID]:
    await _get_item(db, item_id)  # 404 if missing
    rows = (
        await db.execute(
            select(DraftItemObjective.draft_objective_id).where(
                DraftItemObjective.draft_item_id == item_id
            )
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Item media attachments (slice 2 — media in content)
# ---------------------------------------------------------------------------


def _item_media_out(link: DraftItemMedia, asset: MediaAsset) -> ItemMediaOut:
    """Build an ItemMediaOut from a link row + its MediaAsset."""
    return ItemMediaOut(
        media_asset_id=asset.id,
        order_index=link.order_index,
        kind=asset.kind,
        filename=asset.filename,
        mime=asset.mime,
        status=asset.status,
        duration_s=asset.duration_s,
    )


@router.post(
    "/items/{item_id}/media",
    response_model=ItemMediaOut,
    status_code=201,
)
async def attach_media(
    item_id: uuid.UUID,
    body: AttachMediaRequest,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ItemMediaOut:
    """Attach an owned media asset to a draft item.

    * 404 if the item or the asset is missing / not in the caller's org
      (cross-org rows are invisible via the ORM tenant-scope filter).
    * 400 if the asset is not ``ready`` — only confirmed uploads can be
      attached, so a published version never pins a half-uploaded asset.
    * Idempotent: re-attaching the same (item, asset) pair returns the
      existing link instead of tripping the unique constraint.
    """
    item = await _get_item(db, item_id)  # 404 if missing / cross-org

    asset = (
        await db.execute(
            select(MediaAsset).where(MediaAsset.id == body.media_asset_id)
        )
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.status != "ready":
        raise HTTPException(
            status_code=400,
            detail="media asset is not ready (confirm the upload first)",
        )

    existing = (
        await db.execute(
            select(DraftItemMedia).where(
                DraftItemMedia.draft_item_id == item.id,
                DraftItemMedia.media_asset_id == body.media_asset_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _item_media_out(existing, asset)

    link = DraftItemMedia(
        draft_item_id=item.id,
        media_asset_id=body.media_asset_id,
        order_index=body.order_index,
    )
    db.add(link)
    await db.flush()
    out = _item_media_out(link, asset)
    await db.commit()
    return out


@router.get(
    "/items/{item_id}/media",
    response_model=list[ItemMediaOut],
)
async def list_item_media(
    item_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[ItemMediaOut]:
    """List an item's attached media assets, ordered by ``order_index``."""
    await _get_item(db, item_id)  # 404 if missing / cross-org
    rows = (
        await db.execute(
            select(DraftItemMedia, MediaAsset)
            .join(MediaAsset, MediaAsset.id == DraftItemMedia.media_asset_id)
            .where(DraftItemMedia.draft_item_id == item_id)
            .order_by(DraftItemMedia.order_index, DraftItemMedia.created_at)
        )
    ).all()
    return [_item_media_out(link, asset) for link, asset in rows]


@router.delete("/items/{item_id}/media/{asset_id}", status_code=204)
async def detach_media(
    item_id: uuid.UUID,
    asset_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Detach a media asset from a draft item (deletes the link, not the asset).

    404 if the item is missing / cross-org, or the (item, asset) link does not
    exist.
    """
    await _get_item(db, item_id)  # 404 if missing / cross-org
    link = (
        await db.execute(
            select(DraftItemMedia).where(
                DraftItemMedia.draft_item_id == item_id,
                DraftItemMedia.media_asset_id == asset_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Media attachment not found")
    await db.delete(link)
    await db.commit()


# ---------------------------------------------------------------------------
# Dependencies (Task 4)
# ---------------------------------------------------------------------------


@router.post(
    "/courses/{course_id}/dependencies",
    response_model=DependencyOut,
    status_code=201,
)
async def create_dependency(
    course_id: uuid.UUID,
    body: DependencyCreate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> DependencyOut:
    """Add a dependency edge between two items in a draft course.

    * 404 if the course, from_item, or to_item is missing / not in this course.
    * 422 if from_item == to_item (self-loop).
    * 422 if the edge would create a cycle in the dependency DAG.
    * Idempotent: returns the existing row when the (course, from, to) triple
      already exists.
    * Defaults: source="author", accepted=True.
    """
    await _get_course(db, course_id)  # 404 if missing

    # Self-loop check (fast path before DB queries).
    if body.from_item_id == body.to_item_id:
        raise HTTPException(
            status_code=422,
            detail="dependency self-loop: from_item_id and to_item_id must differ",
        )

    # Validate both items exist and belong to this course.
    from_item = (
        await db.execute(select(DraftItem).where(DraftItem.id == body.from_item_id))
    ).scalar_one_or_none()
    if from_item is None or from_item.draft_course_id != course_id:
        raise HTTPException(
            status_code=404, detail="from_item not found in this course"
        )

    to_item = (
        await db.execute(select(DraftItem).where(DraftItem.id == body.to_item_id))
    ).scalar_one_or_none()
    if to_item is None or to_item.draft_course_id != course_id:
        raise HTTPException(
            status_code=404, detail="to_item not found in this course"
        )

    # Idempotency: return existing row if the triple already exists.
    existing = (
        await db.execute(
            select(DraftDependency).where(
                DraftDependency.draft_course_id == course_id,
                DraftDependency.from_item_id == body.from_item_id,
                DraftDependency.to_item_id == body.to_item_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return DependencyOut.model_validate(existing)

    # Cycle check: load all accepted edges for this course then test the proposal.
    current_edges_rows = (
        await db.execute(
            select(DraftDependency.from_item_id, DraftDependency.to_item_id).where(
                DraftDependency.draft_course_id == course_id,
                DraftDependency.accepted == True,  # noqa: E712
            )
        )
    ).all()
    current_edges = [(row.from_item_id, row.to_item_id) for row in current_edges_rows]

    if would_create_cycle(current_edges, body.from_item_id, body.to_item_id):
        raise HTTPException(
            status_code=422, detail="dependency would create a cycle"
        )

    # Insert the new dependency.
    dep = DraftDependency(
        draft_course_id=course_id,
        from_item_id=body.from_item_id,
        to_item_id=body.to_item_id,
        edge_type=body.edge_type,
        source="author",
        accepted=True,
    )
    db.add(dep)
    await db.flush()
    await db.refresh(dep)
    out = DependencyOut.model_validate(dep)
    await db.commit()
    return out


@router.get(
    "/courses/{course_id}/dependencies",
    response_model=list[DependencyOut],
)
async def list_dependencies(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[DependencyOut]:
    await _get_course(db, course_id)  # 404 if missing
    rows = (
        await db.execute(
            select(DraftDependency).where(
                DraftDependency.draft_course_id == course_id
            )
        )
    ).scalars().all()
    return [DependencyOut.model_validate(r) for r in rows]


@router.delete("/dependencies/{dependency_id}", status_code=204)
async def delete_dependency(
    dependency_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> None:
    dep = (
        await db.execute(
            select(DraftDependency).where(DraftDependency.id == dependency_id)
        )
    ).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Dependency not found")
    await db.delete(dep)
    await db.commit()
