"""Task 2 — DraftCourse + DraftObjective CRUD API.

Drives the real ``app.builder.router_course`` handlers against a freshly-created,
RLS-enabled schema (P-006: this test owns its schema — no pre-seeded state). The
role gate is the ``Depends(_AUTHOR_ROLES)`` wrapper, which the harness bypasses
by calling the handlers directly; the bodies only read ``current["sub"]``.

What is asserted:
  * create — POST /courses returns an id, status "drafting".
  * update — PATCH persists the learner_profile JSON.
  * objectives — two objectives with different week/order come back ordered by
    (week_index, order_index).
  * 404 — objectives on / fetch of an unknown course id.
  * isolation — a course created under org A is not listed under org B.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.router_course import (
    create_course,
    create_objective,
    delete_objective,
    get_course,
    list_courses,
    list_objectives,
    update_course,
    update_objective,
)
from app.builder.schemas import (
    CourseCreate,
    CourseUpdate,
    LearnerProfile,
    ObjectiveCreate,
    ObjectiveUpdate,
)
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
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


async def _two_orgs_and_user(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed two orgs + one user in org A. Returns (org_a, org_b, user_id)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.flush()
        user = User(
            email="author@example.com",
            role="instructor",
            organization_id=org_a.id,
        )
        s.add(user)
        await s.commit()
        return org_a.id, org_b.id, user.id


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def test_course_create_update_objectives(rls_engine):
    """Create → defaults; PATCH learner_profile persists; objectives come ordered."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                created = await create_course(
                    CourseCreate(title="Intro to Agents"),
                    current=current,
                    db=session,
                )
                assert created.status == "drafting"
                assert created.title == "Intro to Agents"
                course_id = created.id

                # PATCH the learner_profile — must persist as JSON.
                profile = LearnerProfile(
                    experience_level="beginner",
                    role="student",
                    weekly_hours_target=10.0,
                )
                patched = await update_course(
                    course_id,
                    CourseUpdate(learner_profile=profile, status="ready"),
                    current=current,
                    db=session,
                )
                assert patched.status == "ready"
                assert patched.learner_profile["experience_level"] == "beginner"
                assert patched.learner_profile["weekly_hours_target"] == 10.0

                # Re-fetch confirms persistence.
                fetched = await get_course(course_id, current=current, db=session)
                assert fetched.learner_profile["role"] == "student"

                # Two objectives with different week/order.
                await create_objective(
                    course_id,
                    ObjectiveCreate(
                        text="Second week objective",
                        week_index=2,
                        order_index=0,
                        key_skills=["planning"],
                    ),
                    current=current,
                    db=session,
                )
                await create_objective(
                    course_id,
                    ObjectiveCreate(
                        text="First week objective",
                        bloom_level="apply",
                        week_index=1,
                        order_index=5,
                        key_skills=["tools", "memory"],
                    ),
                    current=current,
                    db=session,
                )

                objs = await list_objectives(
                    course_id, current=current, db=session
                )
                assert [o.text for o in objs] == [
                    "First week objective",
                    "Second week objective",
                ]
                # key_skills round-trips back to a flat list.
                assert objs[0].key_skills == ["tools", "memory"]
                assert objs[0].bloom_level == "apply"
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_unknown_course_id_is_404(rls_engine):
    """GET / objectives on an unknown course id raise 404."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await get_course(uuid.uuid4(), current=current, db=session)
                assert exc.value.status_code == 404

                with pytest.raises(HTTPException) as exc2:
                    await list_objectives(
                        uuid.uuid4(), current=current, db=session
                    )
                assert exc2.value.status_code == 404

                # PATCH / DELETE of an unknown objective id also 404.
                with pytest.raises(HTTPException) as exc3:
                    await update_objective(
                        uuid.uuid4(),
                        ObjectiveUpdate(text="x"),
                        current=current,
                        db=session,
                    )
                assert exc3.value.status_code == 404

                with pytest.raises(HTTPException) as exc4:
                    await delete_objective(
                        uuid.uuid4(), current=current, db=session
                    )
                assert exc4.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_tenant_isolation_on_list(rls_engine):
    """A course created under org A is not listed under org B."""
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    # Create under org A.
    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                created = await create_course(
                    CourseCreate(title="A's course"),
                    current=current,
                    db=session,
                )
                course_id = created.id
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    # List under org B — must NOT see org A's draft.
    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                rows = await list_courses(current=current, db=session)
                assert all(r.id != course_id for r in rows)
                assert rows == []
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    # And org A still sees it.
    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                rows = await list_courses(current=current, db=session)
                assert any(r.id == course_id for r in rows)
            finally:
                await session.close()
    finally:
        current_org.reset(token)
