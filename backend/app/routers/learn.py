"""Router: learner delivery (Phase 2, Foundation 1 — self-paced individual).

The consumption API. A learner enrolls in a **released, immutable**
``CurriculumVersion`` (the curriculum's current ``active_content_version_id`` —
Phase 1's active version), works its items at their own pace, and progress +
completion is tracked per learner. Enrolling **pins** the exact version, so a
re-release never shifts an in-progress learner.

Endpoints (learner-role gated unless noted; all tenant + enrollment scoped —
cross-tenant / cross-learner rows are invisible via the ORM tenant auto-filter
and the ``learner_id == caller`` predicate, so they surface as 404):

  GET  /learn/catalog                          released courses (this tenant)
  POST /learn/enroll                           self-enroll (pins the version)
  GET  /learn/enrollments                      my courses + progress summary
  GET  /learn/courses/{enrollment_id}          pinned structure + presigned media
  GET  /learn/items/{enrollment_id}/{member}   one item + media + my progress
  POST /learn/progress/{enrollment_id}/{member} mark status; recompute completion
  POST /learn/submit/{enrollment_id}/{member}  submit an assessment response
  POST /learn/admin/enroll                     INVITED-ONLY: admin enrolls a learner

Access model (design D1): **invited-only** for v1. A tenant admin
(architect / program_manager) enrolls a named learner via ``/admin/enroll``.
The learner-facing ``/enroll`` lets a learner enroll *themselves* into a course
the tenant has released (the catalog) — never another learner, never a draft.
Media presigning reuses Phase-1 R2 serving over the version's frozen
``media_refs`` (``get_storage`` 503s when storage is unconfigured).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.media.storage import StorageBackend, get_storage
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.deck_artifact import DeckArtifact
from app.models.learner import AssessmentSubmission, Enrollment, LearnerProgress

router = APIRouter(prefix="/api/v1/learn", tags=["learn"])

# Task 2: the learner role. Roles are free-form JWT strings validated at the
# gate (there is no central whitelist — require_roles IS the enforcement point).
_LEARNER = require_roles("learner")
# Invited-only admin enroll: the author/manager tier (mirrors media/release).
_ADMIN_ENROLL = require_roles("architect", "program_manager")

_ProgressStatus = Literal["not_started", "in_progress", "complete"]


# ---------------------------------------------------------------------------
# Pydantic models (inline — mirror media.py style)
# ---------------------------------------------------------------------------


class CatalogEntry(BaseModel):
    curriculum_version_id: uuid.UUID
    curriculum_id: uuid.UUID
    title: str
    version: str  # "major.minor.patch"


class EnrollRequest(BaseModel):
    curriculum_version_id: uuid.UUID


class AdminEnrollRequest(BaseModel):
    learner_id: uuid.UUID
    curriculum_version_id: uuid.UUID


class EnrollmentOut(BaseModel):
    id: uuid.UUID
    curriculum_version_id: uuid.UUID
    learner_id: uuid.UUID
    status: str
    title: str
    completed_items: int
    total_items: int
    enrolled_at: datetime
    completed_at: datetime | None


class MediaRef(BaseModel):
    id: uuid.UUID | None = None
    kind: str | None = None
    filename: str | None = None
    url: str  # fresh presigned GET


class CourseItem(BaseModel):
    member_id: uuid.UUID
    section: str
    week_index: int
    order: int
    kind: str
    lineage_key: str
    content: str
    media: list[MediaRef]
    progress_status: str


class CourseStructure(BaseModel):
    enrollment_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    title: str
    status: str
    completed_items: int
    total_items: int
    items: list[CourseItem]


class ProgressRequest(BaseModel):
    status: _ProgressStatus


class ProgressOut(BaseModel):
    member_id: uuid.UUID
    status: str
    enrollment_status: str
    completed_items: int
    total_items: int


class DeckOut(BaseModel):
    """A rendered deck for the pinned version, with fresh presigned GET URLs.

    The stored ``*_key`` columns never leave the server — URLs are minted fresh
    per request (same discipline as media refs) so they never go stale.
    """

    id: uuid.UUID
    source_member_id: uuid.UUID | None
    status: str
    created_at: datetime
    html_url: str  # fresh presigned GET — embed the slides
    pdf_url: str  # fresh presigned GET — download
    pptx_url: str  # fresh presigned GET — download


class SubmitRequest(BaseModel):
    response_text: str


class SubmissionOut(BaseModel):
    id: uuid.UUID
    enrollment_id: uuid.UUID
    content_member_id: uuid.UUID
    submitted_at: datetime
    score: float | None
    feedback: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _caller_learner_id(current: dict[str, Any]) -> uuid.UUID:
    """The learner's user id (JWT ``sub``). Malformed → 401."""
    try:
        return uuid.UUID(current["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid subject claim") from exc


async def _released_version(
    db: AsyncSession, version_id: uuid.UUID
) -> CurriculumVersion | None:
    """Return *version_id* iff it is some curriculum's current released
    (active) version in the caller's tenant — else ``None`` (auto tenant-scoped)."""
    stmt = (
        select(CurriculumVersion)
        .join(Curriculum, Curriculum.active_content_version_id == CurriculumVersion.id)
        .where(CurriculumVersion.id == version_id)
    )
    return (await db.execute(stmt)).scalars().first()


async def _title_for_version(db: AsyncSession, version: CurriculumVersion) -> str:
    """The owning curriculum's display name (auto tenant-scoped)."""
    curriculum = await db.get(Curriculum, version.curriculum_id)
    return curriculum.name if curriculum is not None else "Untitled course"


async def _total_items(db: AsyncSession, version_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(VersionMember).where(
        VersionMember.curriculum_version_id == version_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def _completed_items(db: AsyncSession, enrollment_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(LearnerProgress).where(
        LearnerProgress.enrollment_id == enrollment_id,
        LearnerProgress.status == "complete",
    )
    return int((await db.execute(stmt)).scalar_one())


async def _load_owned_enrollment(
    db: AsyncSession, enrollment_id: uuid.UUID, learner_id: uuid.UUID
) -> Enrollment:
    """Load the caller's enrollment or 404.

    Both the tenant auto-filter (cross-tenant invisible) and the
    ``learner_id == caller`` predicate (cross-learner invisible) collapse to a
    single 404 — a learner can never observe another learner's enrollment.
    """
    stmt = select(Enrollment).where(
        Enrollment.id == enrollment_id, Enrollment.learner_id == learner_id
    )
    enrollment = (await db.execute(stmt)).scalars().first()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return enrollment


async def _member_in_version(
    db: AsyncSession, member_id: uuid.UUID, version_id: uuid.UUID
) -> VersionMember:
    """Load a VersionMember that belongs to *version_id*, or 404."""
    stmt = select(VersionMember).where(
        VersionMember.id == member_id,
        VersionMember.curriculum_version_id == version_id,
    )
    member = (await db.execute(stmt)).scalars().first()
    if member is None:
        raise HTTPException(status_code=404, detail="Item not found in this course")
    return member


def _present_media(refs: Any, storage: StorageBackend) -> list[MediaRef]:
    """Presign the frozen ``media_refs`` for GET. Tolerates malformed entries."""
    out: list[MediaRef] = []
    if not refs:
        return out
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = ref.get("storage_key")
        if not key:
            continue
        out.append(
            MediaRef(
                id=ref.get("id"),
                kind=ref.get("kind"),
                filename=ref.get("filename"),
                url=storage.presigned_get_url(key),
            )
        )
    return out


def _present_deck(deck: DeckArtifact, storage: StorageBackend) -> DeckOut:
    """Presign a deck's three artifact keys for GET (fresh, never stored)."""
    return DeckOut(
        id=deck.id,
        source_member_id=deck.source_member_id,
        status=deck.status,
        created_at=deck.created_at,
        html_url=storage.presigned_get_url(deck.html_key),
        pdf_url=storage.presigned_get_url(deck.pdf_key),
        pptx_url=storage.presigned_get_url(deck.pptx_key),
    )


async def _recompute_completion(
    db: AsyncSession, enrollment: Enrollment
) -> tuple[int, int]:
    """Sync ``enrollment.status`` to per-item progress. Returns (done, total)."""
    total = await _total_items(db, enrollment.curriculum_version_id)
    done = await _completed_items(db, enrollment.id)
    if total > 0 and done >= total:
        enrollment.status = "completed"
        enrollment.completed_at = datetime.now(timezone.utc)
    elif enrollment.status == "completed":
        # A previously-complete course reverted (item un-completed).
        enrollment.status = "active"
        enrollment.completed_at = None
    return done, total


async def _enrollment_out(db: AsyncSession, enrollment: Enrollment) -> EnrollmentOut:
    version = await db.get(CurriculumVersion, enrollment.curriculum_version_id)
    title = (
        await _title_for_version(db, version)
        if version is not None
        else "Untitled course"
    )
    total = await _total_items(db, enrollment.curriculum_version_id)
    done = await _completed_items(db, enrollment.id)
    return EnrollmentOut(
        id=enrollment.id,
        curriculum_version_id=enrollment.curriculum_version_id,
        learner_id=enrollment.learner_id,
        status=enrollment.status,
        title=title,
        completed_items=done,
        total_items=total,
        enrolled_at=enrollment.enrolled_at,
        completed_at=enrollment.completed_at,
    )


async def _create_enrollment(
    db: AsyncSession, learner_id: uuid.UUID, version_id: uuid.UUID
) -> Enrollment:
    """Pin *version_id* for *learner_id*. 404 if not released; 409 if duplicate."""
    version = await _released_version(db, version_id)
    if version is None:
        raise HTTPException(
            status_code=404, detail="No released course for that version"
        )
    enrollment = Enrollment(learner_id=learner_id, curriculum_version_id=version_id)
    db.add(enrollment)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Learner already enrolled in this course"
        ) from exc
    await db.refresh(enrollment)
    return enrollment


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=list[CatalogEntry])
async def catalog(
    current: dict[str, Any] = Depends(_LEARNER),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> list[CatalogEntry]:
    """Released courses available to enroll in (this tenant).

    Source of truth is ``Curriculum.active_content_version_id`` — exactly one
    released version per curriculum, never a stale or draft version.
    """
    stmt = (
        select(CurriculumVersion, Curriculum)
        .join(
            Curriculum, Curriculum.active_content_version_id == CurriculumVersion.id
        )
        .order_by(Curriculum.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        CatalogEntry(
            curriculum_version_id=v.id,
            curriculum_id=c.id,
            title=c.name,
            version=f"{v.major}.{v.minor}.{v.patch}",
        )
        for v, c in rows
    ]


@router.post("/enroll", response_model=EnrollmentOut, status_code=201)
async def enroll(
    body: EnrollRequest,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentOut:
    """Self-enroll the calling learner into a released course (pins the version)."""
    learner_id = _caller_learner_id(current)
    enrollment = await _create_enrollment(db, learner_id, body.curriculum_version_id)
    return await _enrollment_out(db, enrollment)


@router.post("/admin/enroll", response_model=EnrollmentOut, status_code=201)
async def admin_enroll(
    body: AdminEnrollRequest,
    current: dict[str, Any] = Depends(_ADMIN_ENROLL),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> EnrollmentOut:
    """INVITED-ONLY: a tenant admin enrolls a named learner (pins the version)."""
    enrollment = await _create_enrollment(
        db, body.learner_id, body.curriculum_version_id
    )
    return await _enrollment_out(db, enrollment)


@router.get("/enrollments", response_model=list[EnrollmentOut])
async def my_enrollments(
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
) -> list[EnrollmentOut]:
    """The calling learner's courses + progress summary."""
    learner_id = _caller_learner_id(current)
    stmt = (
        select(Enrollment)
        .where(Enrollment.learner_id == learner_id)
        .order_by(Enrollment.enrolled_at.desc())
    )
    enrollments = (await db.execute(stmt)).scalars().all()
    return [await _enrollment_out(db, e) for e in enrollments]


@router.get("/courses/{enrollment_id}", response_model=CourseStructure)
async def course_structure(
    enrollment_id: uuid.UUID,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> CourseStructure:
    """The enrolled course's structure from the pinned version, with presigned media."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    version_id = enrollment.curriculum_version_id

    stmt = (
        select(VersionMember, ContentVersion, LineageAsset)
        .join(ContentVersion, ContentVersion.id == VersionMember.asset_version_id)
        .join(LineageAsset, LineageAsset.id == VersionMember.asset_id)
        .where(VersionMember.curriculum_version_id == version_id)
        .order_by(VersionMember.week_index, VersionMember.order)
    )
    rows = (await db.execute(stmt)).all()

    # Progress map for this enrollment (member_id -> status).
    prog_stmt = select(LearnerProgress).where(
        LearnerProgress.enrollment_id == enrollment.id
    )
    progress = {
        p.content_member_id: p.status
        for p in (await db.execute(prog_stmt)).scalars().all()
    }

    items = [
        CourseItem(
            member_id=member.id,
            section=member.section,
            week_index=member.week_index,
            order=member.order,
            kind=asset.kind.value if hasattr(asset.kind, "value") else str(asset.kind),
            lineage_key=asset.lineage_key,
            content=content.content,
            media=_present_media(content.media_refs, storage),
            progress_status=progress.get(member.id, "not_started"),
        )
        for member, content, asset in rows
    ]
    done = sum(1 for i in items if i.progress_status == "complete")
    version = await db.get(CurriculumVersion, version_id)
    title = (
        await _title_for_version(db, version)
        if version is not None
        else "Untitled course"
    )
    return CourseStructure(
        enrollment_id=enrollment.id,
        curriculum_version_id=version_id,
        title=title,
        status=enrollment.status,
        completed_items=done,
        total_items=len(items),
        items=items,
    )


@router.get("/courses/{enrollment_id}/decks", response_model=list[DeckOut])
async def course_decks(
    enrollment_id: uuid.UUID,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> list[DeckOut]:
    """The rendered decks for the enrolled course's pinned version.

    Enrollment-scoped: a cross-tenant or cross-learner enrollment id collapses to
    404 (the tenant auto-filter + ``learner_id == caller`` predicate), so decks
    of another tenant's course are never observable. Each deck carries fresh
    presigned GET URLs (html to embed, pdf/pptx to download). Empty list when the
    course has no decks.
    """
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)

    stmt = (
        select(DeckArtifact)
        .where(
            DeckArtifact.curriculum_version_id == enrollment.curriculum_version_id
        )
        .order_by(DeckArtifact.created_at)
    )
    decks = (await db.execute(stmt)).scalars().all()
    return [_present_deck(d, storage) for d in decks]


@router.get("/items/{enrollment_id}/{member_id}", response_model=CourseItem)
async def get_item(
    enrollment_id: uuid.UUID,
    member_id: uuid.UUID,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> CourseItem:
    """A single item's content + presigned media + the caller's progress."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    member = await _member_in_version(db, member_id, enrollment.curriculum_version_id)

    content = await db.get(ContentVersion, member.asset_version_id)
    asset = await db.get(LineageAsset, member.asset_id)
    if content is None or asset is None:
        raise HTTPException(status_code=404, detail="Item content not found")

    prog_stmt = select(LearnerProgress).where(
        LearnerProgress.enrollment_id == enrollment.id,
        LearnerProgress.content_member_id == member.id,
    )
    prog = (await db.execute(prog_stmt)).scalars().first()

    return CourseItem(
        member_id=member.id,
        section=member.section,
        week_index=member.week_index,
        order=member.order,
        kind=asset.kind.value if hasattr(asset.kind, "value") else str(asset.kind),
        lineage_key=asset.lineage_key,
        content=content.content,
        media=_present_media(content.media_refs, storage),
        progress_status=prog.status if prog is not None else "not_started",
    )


@router.post("/progress/{enrollment_id}/{member_id}", response_model=ProgressOut)
async def set_progress(
    enrollment_id: uuid.UUID,
    member_id: uuid.UUID,
    body: ProgressRequest,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
) -> ProgressOut:
    """Mark an item not_started/in_progress/complete; recompute course completion."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    member = await _member_in_version(db, member_id, enrollment.curriculum_version_id)

    prog_stmt = select(LearnerProgress).where(
        LearnerProgress.enrollment_id == enrollment.id,
        LearnerProgress.content_member_id == member.id,
    )
    prog = (await db.execute(prog_stmt)).scalars().first()
    if prog is None:
        prog = LearnerProgress(
            enrollment_id=enrollment.id, content_member_id=member.id
        )
        db.add(prog)
    prog.status = body.status
    prog.completed_at = (
        datetime.now(timezone.utc) if body.status == "complete" else None
    )
    # Flush so the completion recompute counts this row.
    await db.flush()

    done, total = await _recompute_completion(db, enrollment)
    await db.commit()

    return ProgressOut(
        member_id=member.id,
        status=body.status,
        enrollment_status=enrollment.status,
        completed_items=done,
        total_items=total,
    )


@router.post("/submit/{enrollment_id}/{member_id}", response_model=SubmissionOut, status_code=201)
async def submit_assessment(
    enrollment_id: uuid.UUID,
    member_id: uuid.UUID,
    body: SubmitRequest,
    current: dict[str, Any] = Depends(_LEARNER),
    db: AsyncSession = Depends(get_db),
) -> SubmissionOut:
    """Submit a learner's response to an assessment item (scoring via Phase B)."""
    learner_id = _caller_learner_id(current)
    enrollment = await _load_owned_enrollment(db, enrollment_id, learner_id)
    member = await _member_in_version(db, member_id, enrollment.curriculum_version_id)

    submission = AssessmentSubmission(
        enrollment_id=enrollment.id,
        content_member_id=member.id,
        response_text=body.response_text,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)

    return SubmissionOut(
        id=submission.id,
        enrollment_id=submission.enrollment_id,
        content_member_id=submission.content_member_id,
        submitted_at=submission.submitted_at,
        score=submission.score,
        feedback=submission.feedback,
    )
