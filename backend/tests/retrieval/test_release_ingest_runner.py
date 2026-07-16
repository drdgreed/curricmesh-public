"""Background release-ingest runner tests (release→ingest convergence wiring).

``run_ingest`` is the background task the release endpoints schedule once a
``CurriculumVersion`` becomes active. It opens its OWN org-scoped session (the
release request's session is closed by the time it runs), (re)builds the
version's retrieval index via ``ingest_version``, and commits — never raising,
because a background task has no caller to surface an exception to.

What is asserted:
  * PRODUCTION org-scope seam — driving the real ``org_scoped_session`` default,
    the runner opens its own connection, sets its own tenant context, and the
    written ``ContentChunk`` rows are stamped with the runner's org (cross-tenant
    isolation holds with no ambient request context).
  * CI-safe default embedder — with no embedder injected, the runner falls back
    to ``get_embedder()`` (the ``FakeEmbedder`` in CI) and still writes chunks
    fully offline — NO real embedding API call.
  * idempotency — running the runner twice for one version does not duplicate.
  * an empty/no-op version ingests to zero chunks without error.
  * never raises — an embedder that blows up is swallowed (logged), leaving the
    release transaction that already committed untouched.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import FakeEmbedder
from app.core.retrieval.ingest_runner import run_ingest
from app.database import engine as app_engine
from app.models.retrieval import ContentChunk
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import seed_version_with_members


async def _chunk_count(session: AsyncSession, version_id) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ContentChunk)
            .where(ContentChunk.curriculum_version_id == version_id)
        )
    ).scalar_one()


def _scope_for(session: AsyncSession):
    """A session_scope factory yielding ``session``, mirroring org_scoped_session.

    Rolls back on an exception like the production factory does, so the injected
    test session is not left in an aborted state when ingest fails.
    """

    def _factory(_org_id):
        @asynccontextmanager
        async def _cm():
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

        return _cm()

    return _factory


@pytest.mark.asyncio
async def test_run_ingest_production_seam_sets_own_org_scope(db_session: AsyncSession):
    """The runner opens its OWN org_scoped_session and stamps chunks with the org."""
    # Only test driving the module-level app engine (via org_scoped_session):
    # dispose the pool first so it builds fresh connections on THIS loop, and
    # again at the end so nothing loop-bound leaks into the next test.
    await app_engine.dispose()

    version = await seed_version_with_members(
        db_session, texts=["alpha beta gamma", "delta epsilon"]
    )
    await db_session.commit()
    version_id = version.id

    try:
        # REAL production seam: no session_scope override → org_scoped_session.
        await run_ingest(version_id, DEFAULT_ORG_ID, embedder=FakeEmbedder())

        db_session.expire_all()
        rows = (
            await db_session.execute(
                select(ContentChunk).where(
                    ContentChunk.curriculum_version_id == version_id
                )
            )
        ).scalars().all()
        assert rows, "runner wrote no chunks"
        # The runner set the org context on its OWN session → chunks are stamped
        # with that tenant. Isolation holds with no ambient request context.
        for r in rows:
            assert r.organization_id == DEFAULT_ORG_ID
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_run_ingest_default_embedder_is_offline_fake(db_session: AsyncSession):
    """With no embedder injected, the runner uses get_embedder() (Fake in CI)."""
    version = await seed_version_with_members(db_session, texts=["one two three"])
    await db_session.commit()
    version_id = version.id

    # No embedder passed → get_embedder() → FakeEmbedder (offline, no real API).
    await run_ingest(version_id, DEFAULT_ORG_ID, session_scope=_scope_for(db_session))

    db_session.expire_all()
    assert await _chunk_count(db_session, version_id) > 0


@pytest.mark.asyncio
async def test_run_ingest_is_idempotent(db_session: AsyncSession):
    version = await seed_version_with_members(
        db_session, texts=["alpha beta gamma", "delta epsilon zeta"]
    )
    await db_session.commit()
    version_id = version.id
    scope = _scope_for(db_session)

    await run_ingest(version_id, DEFAULT_ORG_ID, embedder=FakeEmbedder(), session_scope=scope)
    db_session.expire_all()
    first = await _chunk_count(db_session, version_id)
    assert first > 0

    await run_ingest(version_id, DEFAULT_ORG_ID, embedder=FakeEmbedder(), session_scope=scope)
    db_session.expire_all()
    second = await _chunk_count(db_session, version_id)
    assert second == first, "re-ingest must not duplicate chunks"


@pytest.mark.asyncio
async def test_run_ingest_empty_version_no_error(db_session: AsyncSession):
    """A version with no members ingests to zero chunks without raising."""
    await run_ingest(
        uuid.uuid4(),
        DEFAULT_ORG_ID,
        embedder=FakeEmbedder(),
        session_scope=_scope_for(db_session),
    )
    # No exception == pass; nothing to assert beyond that.


@pytest.mark.asyncio
async def test_run_ingest_never_raises(db_session: AsyncSession):
    """A failing embedder is swallowed — the runner never propagates."""
    version = await seed_version_with_members(db_session, texts=["boom"])
    await db_session.commit()
    version_id = version.id

    class BoomEmbedder:
        dim = 8

        async def embed(self, texts):
            raise RuntimeError("provider exploded")

    # Must NOT raise — the release already committed; ingest failure is logged.
    await run_ingest(
        version_id,
        DEFAULT_ORG_ID,
        embedder=BoomEmbedder(),
        session_scope=_scope_for(db_session),
    )
    db_session.expire_all()
    assert await _chunk_count(db_session, version_id) == 0
