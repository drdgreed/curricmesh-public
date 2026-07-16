"""Task 3 — DraftItem intake + rule-based categorize + alignment API.

Drives the real ``app.builder.router_course`` item handlers against a freshly
created, RLS-enabled schema (P-006: this test owns its schema). Mirrors the
fixture style of ``test_course_api.py``; the role gate is bypassed by calling
the handlers directly.

What is asserted:
  * intake — POST /items with only {title, content} auto-fills kind via
    ``guess_kind`` and metrics via ``extract_metrics``.
  * 404 — items on / fetch of an unknown course id; PATCH/align on unknown item.
  * alignment — align an item to an objective; it is listed; aligning the SAME
    pair twice is idempotent (no error, one row).
  * cross-course — aligning to an objective in a different course is 404.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.models import DraftItemObjective
from app.builder.router_course import (
    align_item,
    create_course,
    create_item,
    create_objective,
    list_item_objectives,
    list_items,
    update_item,
)
from app.builder.schemas import (
    AlignmentCreate,
    CourseCreate,
    ItemCreate,
    ItemUpdate,
    ObjectiveCreate,
)
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


@pytest.fixture
async def rls_engine():
    """A dedicated engine on a fresh, RLS-enabled schema (no seed needed)."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _org_and_user(engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed one org + one user in it. Returns (org_id, user_id)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Org A")
        s.add(org)
        await s.flush()
        user = User(
            email="author@example.com",
            role="instructor",
            organization_id=org.id,
        )
        s.add(user)
        await s.commit()
        return org.id, user.id


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def test_item_intake_autofills_kind_and_metrics(rls_engine):
    """POST /items with only {title, content} -> kind=slides, metrics word_count."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Intro to Agents"),
                    current=current,
                    db=session,
                )

                item = await create_item(
                    course.id,
                    ItemCreate(title="Week 1 Slides", content="a b c\n---\nd e"),
                    current=current,
                    db=session,
                )
                # kind auto-filled from the title keyword.
                assert item.kind is AssetKind.slides
                # metrics inferred from content ("---" is itself a token).
                assert item.metrics["word_count"] == 6
                assert item.metrics["slide_count"] == 2

                # Listed under the course.
                items = await list_items(course.id, current=current, db=session)
                assert [i.id for i in items] == [item.id]

                # PATCH overrides win.
                patched = await update_item(
                    item.id,
                    ItemUpdate(kind=AssetKind.lesson_plan, week_index=2),
                    current=current,
                    db=session,
                )
                assert patched.kind is AssetKind.lesson_plan
                assert patched.week_index == 2
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_alignment_is_idempotent(rls_engine):
    """Aligning an item to an objective lists it; doing it twice keeps one row."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="C"), current=current, db=session
                )
                obj = await create_objective(
                    course.id,
                    ObjectiveCreate(text="Understand RAG"),
                    current=current,
                    db=session,
                )
                item = await create_item(
                    course.id,
                    ItemCreate(title="RAG Lesson", content="prose here"),
                    current=current,
                    db=session,
                )

                await align_item(
                    item.id,
                    AlignmentCreate(objective_id=obj.id),
                    current=current,
                    db=session,
                )
                # Aligning the SAME pair again must not error.
                await align_item(
                    item.id,
                    AlignmentCreate(objective_id=obj.id),
                    current=current,
                    db=session,
                )

                aligned = await list_item_objectives(
                    item.id, current=current, db=session
                )
                assert aligned == [obj.id]

                # Exactly one join row exists.
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(DraftItemObjective)
                        .where(DraftItemObjective.draft_item_id == item.id)
                    )
                ).scalar_one()
                assert count == 1
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_unknown_course_and_item_are_404(rls_engine):
    """Items on / fetch of unknown course; PATCH/align on unknown item -> 404."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as e1:
                    await create_item(
                        uuid.uuid4(),
                        ItemCreate(title="Orphan"),
                        current=current,
                        db=session,
                    )
                assert e1.value.status_code == 404

                with pytest.raises(HTTPException) as e2:
                    await list_items(uuid.uuid4(), current=current, db=session)
                assert e2.value.status_code == 404

                with pytest.raises(HTTPException) as e3:
                    await update_item(
                        uuid.uuid4(),
                        ItemUpdate(title="x"),
                        current=current,
                        db=session,
                    )
                assert e3.value.status_code == 404

                with pytest.raises(HTTPException) as e4:
                    await align_item(
                        uuid.uuid4(),
                        AlignmentCreate(objective_id=uuid.uuid4()),
                        current=current,
                        db=session,
                    )
                assert e4.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_align_to_objective_in_other_course_is_404(rls_engine):
    """An objective from a different course cannot be aligned (404)."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course_a = await create_course(
                    CourseCreate(title="A"), current=current, db=session
                )
                course_b = await create_course(
                    CourseCreate(title="B"), current=current, db=session
                )
                obj_b = await create_objective(
                    course_b.id,
                    ObjectiveCreate(text="B objective"),
                    current=current,
                    db=session,
                )
                item_a = await create_item(
                    course_a.id,
                    ItemCreate(title="A item"),
                    current=current,
                    db=session,
                )

                with pytest.raises(HTTPException) as exc:
                    await align_item(
                        item_a.id,
                        AlignmentCreate(objective_id=obj_b.id),
                        current=current,
                        db=session,
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
