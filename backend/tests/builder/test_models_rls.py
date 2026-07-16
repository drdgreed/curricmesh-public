"""Task 1 — Course Builder draft model + ORM-level tenant isolation.

Proves the seven mutable ``draft_*`` authoring tables (a) are registered on
``Base.metadata`` and physically created, and (b) are tenant-isolated via
ORM-level ``TenantScoped`` loader criteria + the ``app.current_org`` GUC: a
``DraftCourse`` written under org A is invisible to a session pinned to org B,
yet visible to one pinned to org A. (The local/CI DB role is a superuser, so
DB-enforced RLS is not what produces the isolation here — it is the
``TenantScoped`` filter criteria and the GUC doing the work.)

Per P-006 this test owns its schema: it spins up a dedicated engine, drops +
recreates the schema, applies RLS, and seeds two orgs directly — so it never
depends on pre-seeded state. Org-pinned sessions set ``app.current_org`` via
``set_config`` exactly like the golden suite's ``_open_org_session`` helper.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.org import Organization
from app.tenant import use_org

# The seven draft tables this task introduces.
_DRAFT_TABLES = (
    "draft_courses",
    "draft_objectives",
    "draft_items",
    "draft_item_objectives",
    "draft_dependencies",
    "draft_rubric_results",
    "draft_advisor_notes",
)

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


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    """Open a session whose connection is pinned to ``org_id`` via the GUC."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


def test_models_import():
    """The seven draft models import and are bound to Base.metadata."""
    from app.builder.models import (  # noqa: F401
        DraftAdvisorNote,
        DraftCourse,
        DraftDependency,
        DraftItem,
        DraftItemObjective,
        DraftObjective,
        DraftRubricResult,
    )

    registered = set(Base.metadata.tables)
    for table in _DRAFT_TABLES:
        assert table in registered, f"{table} not registered on Base.metadata"


async def test_all_seven_tables_exist(rls_engine):
    """All seven draft tables are physically present in the schema."""
    factory = async_sessionmaker(rls_engine, class_=AsyncSession)
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        present = {r[0] for r in rows.all()}
    for table in _DRAFT_TABLES:
        assert table in present, f"{table} missing from the physical schema"


async def test_draft_course_tenant_isolation(rls_engine):
    """A DraftCourse under org A is invisible under org B, visible under org A."""
    from app.builder.models import DraftCourse

    # Seed two organizations directly (organizations is NOT RLS-scoped).
    factory = async_sessionmaker(rls_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    # Write a DraftCourse under org A's tenant context (org_id write-stamped).
    with use_org(org_a_id):
        session = await _open_org_session(rls_engine, org_a_id)
        try:
            course = DraftCourse(title="A's course")
            session.add(course)
            await session.commit()
            course_id = course.id
        finally:
            await session.close()

    # Under org B's GUC: the row must NOT be visible (TenantScoped isolation).
    with use_org(org_b_id):
        session = await _open_org_session(rls_engine, org_b_id)
        try:
            count_b = await session.scalar(
                select(func.count())
                .select_from(DraftCourse)
                .where(DraftCourse.id == course_id)
            )
            assert count_b == 0, "org B must not see org A's draft course"
        finally:
            await session.close()

    # Under org A's GUC: the row IS visible.
    with use_org(org_a_id):
        session = await _open_org_session(rls_engine, org_a_id)
        try:
            count_a = await session.scalar(
                select(func.count())
                .select_from(DraftCourse)
                .where(DraftCourse.id == course_id)
            )
            assert count_a == 1, "org A must see its own draft course"
        finally:
            await session.close()
