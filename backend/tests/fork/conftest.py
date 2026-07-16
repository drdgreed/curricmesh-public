"""Shared fixtures + helpers for the ``fork()`` test suite (M4).

Mirrors ``tests/golden/test_graph_manifest_equiv.py::seeded_backfilled_engine``:
each test gets a dedicated async engine on a freshly **seeded + back-filled**
schema (drop/create, RLS applied, real ``seed`` + ``backfill_content_model``), so
the fork is exercised against the same data the manifest read paths use, with no
dependence on any pre-seeded DB (P-006).

The two demo orgs are isolated by RLS + the app-layer ``TenantScoped`` filter, so
every read/write must run inside a tenant context. :func:`org_session` opens a
session pinned to one org via BOTH the ``current_org`` ContextVar (drives the
app-layer filter *and* the ``organization_id`` write-stamp) and the
``app.current_org`` GUC (drives Postgres RLS) — exactly like the golden harness.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.tenant import current_org
from seed.bootcamp_curriculum import seed

# Native PG enum types that drop_all leaves behind — dropped explicitly so a
# re-create doesn't collide on an existing type.
_ENUM_TYPES = ("lifecyclestatus", "assetkind")

SEEDED_SLUGS = ("agentic-ai", "cloud-data-eng")


@pytest.fixture
async def seeded_engine():
    """A dedicated engine on a freshly seeded + back-filled schema."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)

    async with session_factory() as session:
        await seed(session)
        await backfill_content_model(session)

    yield engine
    await engine.dispose()


@asynccontextmanager
async def org_session(engine, org_id: uuid.UUID):
    """Yield a session pinned to ``org_id`` (ContextVar + GUC both set).

    The ContextVar drives both the app-layer ``TenantScoped`` read filter and the
    ``organization_id`` write-stamp; the session-level GUC drives Postgres RLS.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = current_org.set(org_id)
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_org', :org, false)"),
            {"org": str(org_id)},
        )
        try:
            yield session
        finally:
            await session.rollback()
    current_org.reset(token)


async def org_ids(engine) -> list[uuid.UUID]:
    """Every organization id (Organization is not tenant-scoped → unscoped read)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return [r[0] for r in (await s.execute(select(Organization.id))).all()]


async def org_for_slug(engine, slug: str) -> uuid.UUID:
    """Resolve the org id owning ``slug`` (probe each org's tenant context)."""
    for oid in await org_ids(engine):
        async with org_session(engine, oid) as s:
            found = await s.scalar(select(Curriculum.id).where(Curriculum.slug == slug))
            if found is not None:
                return oid
    raise AssertionError(f"seeded curriculum {slug!r} not found in any org")
