"""DELETE /api/v1/builder/courses/{course_id} — draft course deletion.

Drives the real ``app.builder.router_course.delete_course`` handler through the
ASGI transport so the role gate (``_AUTHOR_ROLES``) is exercised. Children are
seeded directly on the ``db_session`` fixture so the cascade assertions are
exact: after the handler commits, every child table must show zero rows for the
deleted course.

Child tables verified:
  * draft_objectives    (FK → draft_courses, ondelete=CASCADE)
  * draft_items         (FK → draft_courses, ondelete=CASCADE)
  * draft_advisor_notes (FK → draft_courses, ondelete=CASCADE)
  * draft_item_media    (FK → draft_items,   ondelete=CASCADE — via item)

Four scenarios:
  1. Happy path — 204, course + all children gone.
  2. Non-existent id → 404.
  3. Cross-org caller (ORG_B token) → 404 (tenant isolation).
  4. Wrong role (student) → 403.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.builder.models import (
    DraftAdvisorNote,
    DraftCourse,
    DraftItem,
    DraftItemMedia,
    DraftObjective,
)
from app.database import get_db
from app.main import app
from app.models.enums import AssetKind
from app.models.media import MediaAsset
from tests.conftest import DEFAULT_ORG_ID

ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    """Wire the ASGI test client to use the fixture session (so the handler
    sees already-committed seed data and its own writes hit the same DB)."""

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str = "architect", org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


async def _seed_course_with_children(db: AsyncSession) -> uuid.UUID:
    """Insert a DraftCourse with one objective, one item, one advisor note,
    and one item-media link. Returns the course id."""
    course = DraftCourse(title="Course To Delete")
    db.add(course)
    await db.flush()

    obj = DraftObjective(
        draft_course_id=course.id,
        text="Understand agents",
        bloom_level="understand",
        order_index=0,
    )
    db.add(obj)
    await db.flush()

    item = DraftItem(
        draft_course_id=course.id,
        kind=AssetKind.lesson_plan,
        title="Intro Lesson",
        order_index=0,
    )
    db.add(item)
    await db.flush()

    note = DraftAdvisorNote(
        draft_course_id=course.id,
        kind="suggestion",
        text="Consider adding more examples.",
        status="open",
    )
    db.add(note)
    await db.flush()

    # Media link requires a MediaAsset row (cross-table).
    asset = MediaAsset(
        kind="video",
        filename="intro.mp4",
        mime="video/mp4",
        storage_key=f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/intro.mp4",
        status="ready",
    )
    db.add(asset)
    await db.flush()

    media_link = DraftItemMedia(
        draft_item_id=item.id,
        media_asset_id=asset.id,
        order_index=0,
    )
    db.add(media_link)

    await db.commit()
    return course.id


@pytest.mark.asyncio
async def test_delete_course_removes_course_and_children(db_session: AsyncSession):
    """204 on valid delete; course and ALL children are gone from the DB."""
    course_id = await _seed_course_with_children(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.delete(
            f"/api/v1/builder/courses/{course_id}", headers=_auth()
        )
    assert resp.status_code == 204, resp.text

    # Expire session identity map so queries hit the DB.
    db_session.expire_all()

    # Course itself is gone.
    gone = (
        await db_session.execute(
            select(DraftCourse).where(DraftCourse.id == course_id)
        )
    ).scalar_one_or_none()
    assert gone is None, "DraftCourse row should have been deleted"

    # All child rows are gone (DB-cascade).
    obj_count = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftObjective)
            .where(DraftObjective.draft_course_id == course_id)
        )
    ).scalar_one()
    assert obj_count == 0, f"Expected 0 DraftObjective rows, got {obj_count}"

    item_count = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftItem)
            .where(DraftItem.draft_course_id == course_id)
        )
    ).scalar_one()
    assert item_count == 0, f"Expected 0 DraftItem rows, got {item_count}"

    note_count = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftAdvisorNote)
            .where(DraftAdvisorNote.draft_course_id == course_id)
        )
    ).scalar_one()
    assert note_count == 0, f"Expected 0 DraftAdvisorNote rows, got {note_count}"

    # DraftItemMedia cascades via draft_items — all items were already deleted,
    # so the FK constraint ensures media links are gone too. Verify directly.
    media_count = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftItemMedia)
            .join(DraftItem, DraftItem.id == DraftItemMedia.draft_item_id)
            .where(DraftItem.draft_course_id == course_id)
        )
    ).scalar_one()
    assert media_count == 0, f"Expected 0 DraftItemMedia rows, got {media_count}"


@pytest.mark.asyncio
async def test_delete_course_nonexistent_404(db_session: AsyncSession):
    """DELETE on an unknown UUID returns 404."""
    async with _make_transport(db_session) as client:
        resp = await client.delete(
            f"/api/v1/builder/courses/{uuid.uuid4()}", headers=_auth()
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_course_cross_org_404(db_session: AsyncSession):
    """A course seeded under DEFAULT_ORG is invisible to an ORG_B caller → 404."""
    course_id = await _seed_course_with_children(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.delete(
            f"/api/v1/builder/courses/{course_id}",
            headers=_auth(org=ORG_B),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_course_wrong_role_403(db_session: AsyncSession):
    """A caller with role 'student' (not in _AUTHOR_ROLES) is rejected with 403."""
    course_id = await _seed_course_with_children(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.delete(
            f"/api/v1/builder/courses/{course_id}",
            headers=_auth(role="student"),
        )
    assert resp.status_code == 403
