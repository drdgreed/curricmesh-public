"""Fixtures for the golden-baseline harness.

Unlike the top-level ``tests/conftest.py`` (which drops + recreates an EMPTY
schema per test), this harness reads a **pre-seeded** database — the one you
stand up for Task G:

    docker run -d -p <port>:5432 ... postgres:16
    DATABASE_URL=...:<port>/... alembic upgrade head
    DATABASE_URL=...:<port>/... python -m seed.bootcamp_curriculum

The seed creates two tenants (Career Forge + Acme Academy). Because every domain
row is RLS-scoped + app-layer-filtered on ``organization_id``, a read must run
inside that tenant's context. :func:`org_scoped_session` yields a session pinned
to one org via BOTH the ``current_org`` ContextVar (drives the app-layer filter)
and the ``app.current_org`` GUC (drives RLS).

These fixtures are skipped automatically when the seeded curricula aren't present
(e.g. CI without a seeded DB), so the suite never hard-fails on environment.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.tenant import current_org

# Import all models so they register on Base.metadata.
import app.models  # noqa: F401


# The two seeded curricula, keyed by the stable identifiers the fixtures pin on.
SEEDED_CURRICULA = {
    "agentic-ai": "Career Forge",
    "cloud-data-eng": "Acme Academy",
}


@asynccontextmanager
async def org_scoped_session(engine, org_id: uuid.UUID):
    """Yield a session pinned to ``org_id`` (ContextVar + GUC both set)."""
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    token = current_org.set(org_id)
    async with session_factory() as session:
        # Session-level set_config (is_local=false) so it survives the implicit
        # transaction boundaries the read paths open.
        await session.execute(
            text("SELECT set_config('app.current_org', :org, false)"),
            {"org": str(org_id)},
        )
        try:
            yield session
        finally:
            await session.rollback()
    current_org.reset(token)


async def _org_id_for_curriculum(engine, slug: str) -> uuid.UUID | None:
    """Resolve the org id owning ``slug`` by scanning each org (unscoped lookup).

    Organizations isn't tenant-scoped, so we can list orgs unscoped, then probe
    each one's tenant context for the curriculum slug.
    """
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        org_rows = await session.execute(select(Organization.id))
        org_ids = [row[0] for row in org_rows.all()]

    for oid in org_ids:
        async with org_scoped_session(engine, oid) as s:
            found = await s.execute(
                select(Curriculum.id).where(Curriculum.slug == slug)
            )
            if found.scalar_one_or_none() is not None:
                return oid
    return None


@pytest.fixture
async def golden_engine():
    """An async engine bound to the seeded DB (settings.DATABASE_URL from .env)."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def require_seeded(golden_engine):
    """Skip the test unless BOTH seeded curricula are present.

    Returns a mapping ``slug -> org_id`` for the seeded curricula.
    """
    mapping: dict[str, uuid.UUID] = {}
    try:
        for slug in SEEDED_CURRICULA:
            oid = await _org_id_for_curriculum(golden_engine, slug)
            if oid is None:
                pytest.skip(
                    f"seeded curriculum {slug!r} not found — run "
                    "`python -m seed.bootcamp_curriculum` against a dedicated DB first"
                )
            mapping[slug] = oid
    except Exception as exc:  # DB unreachable / schema missing
        pytest.skip(f"seeded DB not reachable: {exc}")
    return mapping
