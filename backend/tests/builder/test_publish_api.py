"""Task 5+6 — GET /effort and GET /overload endpoint integration tests.

Drives ``router_publish`` handlers directly (no HTTP layer) against a real,
RLS-enabled schema.  Fixture style mirrors ``test_course_api.py`` / ``test_items_api.py``.

What is asserted:
  * effort — week-1 bucket sums slides + lab minutes correctly.
  * overload — the returned flag row for week 1 has the right overload boolean
    given the learner_profile's weekly_hours_target.
  * 404 — both endpoints raise 404 on an unknown course id.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.effort import DEFAULT_RATES
from app.builder.models import DraftCourse, DraftItem, DraftObjective
from app.builder.router_publish import get_effort, get_overload
from app.builder.schemas import CourseCreate
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def rls_engine():
    """Fresh, RLS-enabled schema — owned by this test module."""
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
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Test Org")
        s.add(org)
        await s.flush()
        user = User(
            email="author@test.com",
            role="instructor",
            organization_id=org.id,
        )
        s.add(user)
        await s.commit()
        return org.id, user.id


async def _open_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_course(
    session: AsyncSession,
    org_id: uuid.UUID,
    weekly_hours_target: float = 10.0,
) -> uuid.UUID:
    """Insert a DraftCourse with a learner_profile and return its id."""
    course = DraftCourse(
        organization_id=org_id,
        title="Test Course",
        learner_profile={"weekly_hours_target": weekly_hours_target},
        effort_config=None,
        status="drafting",
    )
    session.add(course)
    await session.flush()
    await session.refresh(course)
    await session.commit()
    return course.id


async def _seed_items_and_objectives(
    session: AsyncSession,
    org_id: uuid.UUID,
    course_id: uuid.UUID,
) -> None:
    """Seed two items in week 1 and two objectives also in week 1.

    Item 1: 10 slides  → 10 min  (review_min_per_slide=1.0)
    Item 2: 300 LOC    → 135 min (min_per_100_loc=45.0)
    Total week 1: 145 min = 2.42 h
    With target 10 h → overload=False; target 2 h → overload=True.
    """
    item_slides = DraftItem(
        organization_id=org_id,
        draft_course_id=course_id,
        kind=AssetKind.slides,
        title="Intro slides",
        metrics={"slide_count": 10},
        week_index=1,
        order_index=0,
    )
    item_lab = DraftItem(
        organization_id=org_id,
        draft_course_id=course_id,
        kind=AssetKind.lab,
        title="First lab",
        metrics={"lines_of_code": 300},
        week_index=1,
        order_index=1,
    )
    obj1 = DraftObjective(
        organization_id=org_id,
        draft_course_id=course_id,
        text="Understand basics",
        week_index=1,
        order_index=0,
    )
    obj2 = DraftObjective(
        organization_id=org_id,
        draft_course_id=course_id,
        text="Apply concepts",
        week_index=1,
        order_index=1,
    )
    session.add_all([item_slides, item_lab, obj1, obj2])
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_effort_returns_week1_bucket(rls_engine):
    """GET /courses/{id}/effort returns week-1 with 145 summed minutes."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course_id = await _seed_course(session, org_id)
                await _seed_items_and_objectives(session, org_id, course_id)

                result = await get_effort(course_id, current=current, db=session)

                assert "by_week" in result
                assert "total_student_minutes" in result

                week1 = result["by_week"].get(1)
                assert week1 is not None, "expected a week-1 bucket"
                assert week1["student_minutes"] == 145  # 10 + 135
                assert week1["item_count"] == 2
                assert result["total_student_minutes"] == 145
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_overload_false_when_target_is_10h(rls_engine):
    """145 min = 2.42 h < 10 h target → overload False."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course_id = await _seed_course(session, org_id, weekly_hours_target=10.0)
                await _seed_items_and_objectives(session, org_id, course_id)

                flags = await get_overload(course_id, current=current, db=session)

                assert isinstance(flags, list)
                week1_flag = next((f for f in flags if f["week"] == 1), None)
                assert week1_flag is not None
                assert week1_flag["overload"] is False
                assert week1_flag["student_hours"] == round(145 / 60, 2)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_overload_true_when_target_is_2h(rls_engine):
    """145 min = 2.42 h > 2 h target → overload True."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course_id = await _seed_course(session, org_id, weekly_hours_target=2.0)
                await _seed_items_and_objectives(session, org_id, course_id)

                flags = await get_overload(course_id, current=current, db=session)

                week1_flag = next((f for f in flags if f["week"] == 1), None)
                assert week1_flag is not None
                assert week1_flag["overload"] is True
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_overload_density_warn_from_objectives_and_hard_items(rls_engine):
    """2 objectives + 1 lab (HARD_KIND) = 3 new_concepts.
    density_threshold defaults to 4, so density_warn=False.
    Increasing objectives to 4 total (4 + 1 lab = 5) would warn — but here
    we just confirm the count is 3 (2 objectives + 1 lab)."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course_id = await _seed_course(session, org_id, weekly_hours_target=10.0)
                await _seed_items_and_objectives(session, org_id, course_id)

                flags = await get_overload(course_id, current=current, db=session)

                week1_flag = next((f for f in flags if f["week"] == 1), None)
                assert week1_flag is not None
                # 2 objectives + 1 lab (slides is not HARD) = 3
                assert week1_flag["new_concepts"] == 3
                assert week1_flag["density_warn"] is False  # 3 <= 4
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_effort_404_on_unknown_course(rls_engine):
    """GET /effort on a non-existent course raises 404."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                with pytest.raises(HTTPException) as exc:
                    await get_effort(uuid.uuid4(), current=current, db=session)
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_overload_404_on_unknown_course(rls_engine):
    """GET /overload on a non-existent course raises 404."""
    org_id, user_id = await _org_and_user(rls_engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                with pytest.raises(HTTPException) as exc:
                    await get_overload(uuid.uuid4(), current=current, db=session)
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
