"""
Pytest fixtures for CurricMesh integration tests.
Uses SQLAlchemy 2.0 async + pytest-asyncio in auto mode.

Design notes:
- pytest-asyncio creates a new event loop per test by default (function scope).
- The engine and session must be created on the SAME loop as the test coroutine.
- We therefore create the engine inside the db_session fixture (function scope)
  so everything shares one loop per test.
- PostgreSQL native enum types (lifecyclestatus, assetkind) must be dropped
  with tables. We drop/recreate all tables in Base.metadata plus their
  associated enum types, leaving the alembic_version bookkeeping table intact.

Multi-tenancy (MT3):
- Every test runs inside a fixed DEFAULT_ORG tenant context. After recreating
  the schema we (a) insert the DEFAULT_ORG ``organizations`` row, (b) apply the
  RLS policies so the 13 domain tables enforce tenant isolation, and (c) pin the
  session's ``app.current_org`` GUC to DEFAULT_ORG.
- We also bind the ``current_org`` ContextVar to DEFAULT_ORG for the test's
  duration, so the ``organization_id`` column default (``require_org()``) stamps
  every inserted domain row with DEFAULT_ORG. GUC and ContextVar agree, so reads
  see exactly the rows the test wrote.
- The test DB role is a *superuser*, which bypasses RLS even under FORCE. So the
  GUC mechanism keeps existing tests green, but cross-tenant *read filtering*
  can only be proven by querying as a non-superuser — see ``rls_probe`` below,
  which is what actually demonstrates RLS is live.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.tenant import current_org

# Import all models so they register on Base.metadata before create_all runs.
import app.models  # noqa: F401

# A fixed tenant every test operates within. Importable by test modules so their
# JWT helpers can mint tokens carrying the matching ``org`` claim.
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000d6fa1")

# Collect the native PG enum type names declared by the models so we can
# drop them before recreating tables.
_ENUM_TYPES = ("lifecyclestatus", "assetkind")


@pytest.fixture
async def db_session():
    """
    Yield an AsyncSession for one test, scoped to DEFAULT_ORG under live RLS.

    Schema lifecycle per test:
      1. Drop all tables in Base.metadata (app tables only; leaves alembic_version).
      2. Drop native PG enum types.
      3. Recreate tables via create_all.
      4. Seed the DEFAULT_ORG organization row and apply RLS policies.
      5. Pin the session GUC + ContextVar to DEFAULT_ORG.
      6. Yield session.
      7. Reset the ContextVar; rollback any uncommitted changes.
      8. Dispose the engine.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        # pgvector extension: required by the retrieval infra's Vector column +
        # its HNSW index. Idempotent; the test image (pgvector/pgvector:pg16)
        # ships the extension. Created before create_all so the Vector-typed
        # ``content_chunks`` table and its ANN index build.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Drop app tables in dependency order.
        await conn.run_sync(Base.metadata.drop_all)
        # Drop PG native enum types that aren't removed by drop_all.
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        # Rebuild.
        await conn.run_sync(Base.metadata.create_all)
        # organizations is NOT RLS-scoped, so this insert is unconstrained.
        await conn.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
            {"id": str(DEFAULT_ORG_ID), "name": "Default Test Org"},
        )
        # Enable tenant-isolation RLS on the 13 domain tables.
        await conn.run_sync(apply_rls)

    # Bind the ContextVar so the organization_id column default stamps rows with
    # DEFAULT_ORG on insert. Reset in teardown to avoid leaking across tests.
    token = current_org.set(DEFAULT_ORG_ID)

    async with session_factory() as session:
        # Session-level set_config (is_local=false) persists across this
        # session's commits/rollbacks, so every implicit transaction the test
        # opens inherits DEFAULT_ORG as its app.current_org GUC.
        await session.execute(
            text("SELECT set_config('app.current_org', :org, false)"),
            {"org": str(DEFAULT_ORG_ID)},
        )
        try:
            yield session
        finally:
            await session.rollback()

    current_org.reset(token)
    await engine.dispose()
