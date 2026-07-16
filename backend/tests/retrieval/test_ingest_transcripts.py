"""Media-transcript ingestion tests (transcript→index wiring).

``ingest_version`` indexes not only each member's item TEXT but also the
transcripts of the media its content pins (``ContentVersion.media_refs`` →
``MediaAsset`` id → ``MediaTranscript.text``), as
``ContentChunk(kind="media_transcript")``. Covers, against the real Postgres
schema with pgvector under DEFAULT_ORG:

  - a member whose item embeds a transcribed asset → media_transcript chunks
    exist for THAT member and ``retrieve`` can return them;
  - a referenced asset WITHOUT a transcript → no media chunks (graceful);
  - re-ingest is idempotent (no duplicate transcript chunks);
  - tenant isolation: another org never sees a version's transcript chunks.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.retrieval.embedder import FakeEmbedder
from app.core.retrieval.ingest import ingest_version
from app.core.retrieval.retrieve import retrieve
from app.models.retrieval import ContentChunk
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import (
    media_ref,
    seed_media_asset,
    seed_version_with_media,
)


async def _kind_count(session: AsyncSession, version_id, kind: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ContentChunk)
            .where(
                ContentChunk.curriculum_version_id == version_id,
                ContentChunk.kind == kind,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_transcript_indexed_for_referencing_member(db_session: AsyncSession):
    transcript = "welcome to the agentic architecture deep dive on tool use"
    asset = await seed_media_asset(db_session, transcript_text=transcript)
    version, member = await seed_version_with_media(
        db_session,
        item_text="lesson body text about agents",
        media_refs=[media_ref(asset)],
    )
    emb = FakeEmbedder()
    await ingest_version(db_session, version.id, emb)

    media_chunks = (
        await db_session.execute(
            select(ContentChunk).where(
                ContentChunk.curriculum_version_id == version.id,
                ContentChunk.kind == "media_transcript",
            )
        )
    ).scalars().all()
    assert media_chunks, "transcript should have produced media_transcript chunks"
    for c in media_chunks:
        assert c.source_member_id == member.id
        assert len(list(c.embedding)) == settings.EMBEDDING_DIM
        assert c.token_count > 0
    # Text chunks still indexed alongside (both kinds present).
    assert await _kind_count(db_session, version.id, "text") > 0

    # The transcript is retrievable via an exact-text query (FakeEmbedder → d=0).
    hits = await retrieve(db_session, version.id, transcript, k=5, embedder=emb)
    assert any(
        h.kind == "media_transcript" and h.text == transcript for h in hits
    ), "the transcript chunk must be retrievable"


@pytest.mark.asyncio
async def test_media_without_transcript_produces_no_media_chunks(
    db_session: AsyncSession,
):
    asset = await seed_media_asset(db_session, transcript_text=None)
    version, _ = await seed_version_with_media(
        db_session,
        item_text="lesson body only",
        media_refs=[media_ref(asset)],
    )
    await ingest_version(db_session, version.id, FakeEmbedder())

    assert await _kind_count(db_session, version.id, "media_transcript") == 0
    assert await _kind_count(db_session, version.id, "text") > 0


@pytest.mark.asyncio
async def test_no_media_refs_produces_no_media_chunks(db_session: AsyncSession):
    version, _ = await seed_version_with_media(
        db_session, item_text="text only lesson", media_refs=None
    )
    await ingest_version(db_session, version.id, FakeEmbedder())
    assert await _kind_count(db_session, version.id, "media_transcript") == 0


@pytest.mark.asyncio
async def test_transcript_ingest_is_idempotent(db_session: AsyncSession):
    asset = await seed_media_asset(
        db_session, transcript_text="idempotent transcript body one two three"
    )
    version, _ = await seed_version_with_media(
        db_session,
        item_text="body",
        media_refs=[media_ref(asset)],
    )
    n1 = await ingest_version(db_session, version.id, FakeEmbedder())
    media1 = await _kind_count(db_session, version.id, "media_transcript")
    assert media1 > 0

    n2 = await ingest_version(db_session, version.id, FakeEmbedder())
    media2 = await _kind_count(db_session, version.id, "media_transcript")
    assert n2 == n1
    assert media2 == media1, "re-ingest must not duplicate transcript chunks"


@pytest.mark.asyncio
async def test_malformed_media_ref_is_ignored(db_session: AsyncSession):
    """A ref missing/garbage ``media_asset_id`` never breaks ingestion."""
    version, _ = await seed_version_with_media(
        db_session,
        item_text="body",
        media_refs=[{"storage_key": "x", "kind": "video"}, {"media_asset_id": "nope"}],
    )
    n = await ingest_version(db_session, version.id, FakeEmbedder())
    assert n > 0  # text still indexed
    assert await _kind_count(db_session, version.id, "media_transcript") == 0


@pytest.mark.asyncio
async def test_transcript_tenant_isolation(db_session: AsyncSession):
    """Org B never sees org A's transcript chunks, even with A's version id."""
    emb = FakeEmbedder()
    secret = "org-a-confidential-transcript-secret words"
    asset = await seed_media_asset(db_session, transcript_text=secret)
    version_a, _ = await seed_version_with_media(
        db_session, item_text="a body", media_refs=[media_ref(asset)]
    )
    await ingest_version(db_session, version_a.id, emb)
    await db_session.commit()

    org_b = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
        {"id": str(org_b), "name": "Org B"},
    )
    await db_session.commit()

    await db_session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_b)},
    )
    with use_org(org_b):
        hits = await retrieve(db_session, version_a.id, secret, k=10, embedder=emb)
    assert hits == [], "org B must not see org A's transcript chunks"

    await db_session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(DEFAULT_ORG_ID)},
    )
