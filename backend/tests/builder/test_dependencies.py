"""Task 4 — DraftDependency CRUD + cycle detection.

Tests the dependency endpoints against a real, RLS-enabled schema (same
fixture pattern as ``test_items_api.py``). Covers:

* A→B, B→C: both 201 and stored.
* A→B again: idempotent — same row returned, exactly one DB row.
* C→A: cycle → 422, no row created.
* A→A: self-loop → 422.
* from_item from another course → 404.
* DELETE removes one edge; the other is unaffected.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.models import DraftDependency
from app.builder.router_course import (
    create_course,
    create_dependency,
    create_item,
    delete_dependency,
    list_dependencies,
)
from app.builder.schemas import CourseCreate, DependencyCreate, ItemCreate
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


async def _org_and_user(engine) -> tuple[uuid.UUID, uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Dep Org")
        s.add(org)
        await s.flush()
        user = User(
            email="dep_author@example.com",
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


async def _setup_abc(
    engine,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[AsyncSession, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create one course and three items A, B, C. Returns (session, course_id, A, B, C)."""
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}
    session = await _open_org_session(engine, org_id)
    course = await create_course(
        CourseCreate(title="Dep Course"), current=current, db=session
    )
    item_a = await create_item(
        course.id, ItemCreate(title="Item A"), current=current, db=session
    )
    item_b = await create_item(
        course.id, ItemCreate(title="Item B"), current=current, db=session
    )
    item_c = await create_item(
        course.id, ItemCreate(title="Item C"), current=current, db=session
    )
    return session, course.id, item_a.id, item_b.id, item_c.id


async def test_add_linear_chain_and_list(rls_engine):
    """A→B and B→C are both 201; GET lists them."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                dep_ab = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=a, to_item_id=b),
                    current=current,
                    db=session,
                )
                assert dep_ab.from_item_id == a
                assert dep_ab.to_item_id == b
                assert dep_ab.source == "author"
                assert dep_ab.accepted is True

                dep_bc = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=b, to_item_id=c),
                    current=current,
                    db=session,
                )
                assert dep_bc.from_item_id == b
                assert dep_bc.to_item_id == c

                deps = await list_dependencies(course_id, current=current, db=session)
                assert len(deps) == 2
                ids = {d.id for d in deps}
                assert dep_ab.id in ids
                assert dep_bc.id in ids
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_add_same_dependency_is_idempotent(rls_engine):
    """Adding A→B twice returns the same row; exactly one DB row exists."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                dep_first = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=a, to_item_id=b),
                    current=current,
                    db=session,
                )
                dep_second = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=a, to_item_id=b),
                    current=current,
                    db=session,
                )
                # Same id returned.
                assert dep_first.id == dep_second.id

                # Exactly one row in DB.
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(DraftDependency)
                        .where(
                            DraftDependency.draft_course_id == course_id,
                            DraftDependency.from_item_id == a,
                            DraftDependency.to_item_id == b,
                        )
                    )
                ).scalar_one()
                assert count == 1
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_cycle_is_rejected_with_422(rls_engine):
    """A→B, B→C in place; C→A must be 422 and leave no row."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=a, to_item_id=b),
                    current=current,
                    db=session,
                )
                await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=b, to_item_id=c),
                    current=current,
                    db=session,
                )

                with pytest.raises(HTTPException) as exc:
                    await create_dependency(
                        course_id,
                        DependencyCreate(from_item_id=c, to_item_id=a),
                        current=current,
                        db=session,
                    )
                assert exc.value.status_code == 422
                assert "cycle" in exc.value.detail.lower()

                # No C→A row was created.
                bad_row = (
                    await session.execute(
                        select(DraftDependency).where(
                            DraftDependency.draft_course_id == course_id,
                            DraftDependency.from_item_id == c,
                            DraftDependency.to_item_id == a,
                        )
                    )
                ).scalar_one_or_none()
                assert bad_row is None
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_self_loop_is_422(rls_engine):
    """A→A self-loop must be rejected with 422."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                with pytest.raises(HTTPException) as exc:
                    await create_dependency(
                        course_id,
                        DependencyCreate(from_item_id=a, to_item_id=a),
                        current=current,
                        db=session,
                    )
                assert exc.value.status_code == 422
                assert "self-loop" in exc.value.detail.lower()
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_from_item_from_other_course_is_404(rls_engine):
    """Using a from_item that belongs to a different course must 404."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                # Create a second course with its own item.
                course_x = await create_course(
                    CourseCreate(title="Other Course"), current=current, db=session
                )
                item_x = await create_item(
                    course_x.id,
                    ItemCreate(title="X Item"),
                    current=current,
                    db=session,
                )

                with pytest.raises(HTTPException) as exc:
                    await create_dependency(
                        course_id,
                        DependencyCreate(from_item_id=item_x.id, to_item_id=b),
                        current=current,
                        db=session,
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_delete_removes_one_dependency(rls_engine):
    """DELETE /dependencies/{id} removes exactly one edge; the other survives."""
    engine = rls_engine
    org_id, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session, course_id, a, b, c = await _setup_abc(engine, org_id, user_id)
            try:
                dep_ab = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=a, to_item_id=b),
                    current=current,
                    db=session,
                )
                dep_bc = await create_dependency(
                    course_id,
                    DependencyCreate(from_item_id=b, to_item_id=c),
                    current=current,
                    db=session,
                )

                await delete_dependency(dep_ab.id, current=current, db=session)

                deps = await list_dependencies(course_id, current=current, db=session)
                assert len(deps) == 1
                assert deps[0].id == dep_bc.id

                # Deleting a non-existent id → 404.
                with pytest.raises(HTTPException) as exc:
                    await delete_dependency(
                        uuid.uuid4(), current=current, db=session
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
