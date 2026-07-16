"""Model round-trip for ``ContentChunk`` (Phase B retrieval infra, Task 1).

Proves the tenant-scoped, version-pinned chunk table persists against the real
Postgres schema (with the pgvector extension) under the DEFAULT_ORG context:
a row inserts and reads back, including its ``Vector`` embedding.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.content_model import VersionMember
from app.models.retrieval import ContentChunk
from tests.retrieval._helpers import seed_version_with_members


@pytest.mark.asyncio
async def test_content_chunk_round_trip(db_session: AsyncSession):
    version = await seed_version_with_members(db_session, texts=["hello world"])
    vm = (
        await db_session.execute(
            select(VersionMember).where(
                VersionMember.curriculum_version_id == version.id
            )
        )
    ).scalar_one()

    vec = [0.1] * settings.EMBEDDING_DIM
    chunk = ContentChunk(
        curriculum_version_id=version.id,
        source_member_id=vm.id,
        kind="text",
        text="hello world",
        embedding=vec,
        token_count=2,
    )
    db_session.add(chunk)
    await db_session.flush()
    # Re-read from the DB (async refresh) so we prove the row round-trips,
    # including the Vector embedding, not just the in-session object.
    await db_session.refresh(chunk)
    got = chunk
    assert got.curriculum_version_id == version.id
    assert got.source_member_id == vm.id
    assert got.kind == "text"
    assert got.text == "hello world"
    assert got.token_count == 2
    assert len(list(got.embedding)) == settings.EMBEDDING_DIM
    # organization_id is write-stamped from the ambient DEFAULT_ORG context.
    assert got.organization_id is not None
