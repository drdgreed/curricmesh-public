"""ingest_version tests (Phase B retrieval infra, Task 3).

Covers, against the real Postgres schema with pgvector under DEFAULT_ORG:
  - ingestion chunks every member's content, embeds it, and writes ContentChunk
    rows pinned to the version + source member, with the right dim/kind;
  - ingestion is IDEMPOTENT per version — re-running yields the same chunk set,
    not duplicates;
  - a version with no members ingests to zero chunks.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.retrieval.embedder import FakeEmbedder
from app.core.retrieval.ingest import ingest_version
from app.models.content_model import CurriculumVersion, VersionMember
from app.models.enums import LifecycleStatus
from app.models.retrieval import ContentChunk
from tests.retrieval._helpers import seed_version_with_members


async def _chunk_count(session: AsyncSession, version_id) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ContentChunk)
            .where(ContentChunk.curriculum_version_id == version_id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_ingest_writes_chunks(db_session: AsyncSession):
    version = await seed_version_with_members(
        db_session, texts=["alpha beta gamma", "delta epsilon"]
    )
    n = await ingest_version(db_session, version.id, FakeEmbedder())
    assert n > 0

    rows = (
        await db_session.execute(
            select(ContentChunk).where(
                ContentChunk.curriculum_version_id == version.id
            )
        )
    ).scalars().all()
    assert len(rows) == n
    for r in rows:
        assert r.kind == "text"
        assert r.curriculum_version_id == version.id
        assert r.source_member_id is not None
        assert len(list(r.embedding)) == settings.EMBEDDING_DIM
        assert r.token_count > 0
    # Every source member is represented.
    member_ids = {
        m.id
        for m in (
            await db_session.execute(
                select(VersionMember).where(
                    VersionMember.curriculum_version_id == version.id
                )
            )
        ).scalars()
    }
    assert {r.source_member_id for r in rows} == member_ids


@pytest.mark.asyncio
async def test_ingest_is_idempotent(db_session: AsyncSession):
    version = await seed_version_with_members(
        db_session, texts=["alpha beta gamma", "delta epsilon zeta"]
    )
    n1 = await ingest_version(db_session, version.id, FakeEmbedder())
    after_first = await _chunk_count(db_session, version.id)
    assert after_first == n1

    n2 = await ingest_version(db_session, version.id, FakeEmbedder())
    after_second = await _chunk_count(db_session, version.id)
    assert n2 == n1
    assert after_second == n1, "re-ingest must not duplicate chunks"


@pytest.mark.asyncio
async def test_ingest_empty_version(db_session: AsyncSession):
    version = CurriculumVersion(
        curriculum_id=(
            await seed_version_with_members(db_session, texts=["x"])
        ).curriculum_id,
        major=2,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    db_session.add(version)
    await db_session.flush()
    n = await ingest_version(db_session, version.id, FakeEmbedder())
    assert n == 0
    assert await _chunk_count(db_session, version.id) == 0
