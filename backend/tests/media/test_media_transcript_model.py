"""Model + tenant-isolation tests for ``MediaTranscript`` (Phase B, B2).

``MediaTranscript`` stores the extracted/transcribed text for a ``MediaAsset``
(one per asset — ``media_asset_id`` is UNIQUE; re-transcribe replaces). Like
every authoring table it is ``TenantScoped`` and joins the RLS regime.

The round-trip runs under the DEFAULT_ORG ``db_session`` fixture; the isolation
test owns its schema + two orgs (mirrors ``test_item_media_model.py``).
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
from app.models.media import MediaAsset, MediaTranscript
from app.models.org import Organization
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


def _asset(org_id: uuid.UUID, *, kind: str = "audio") -> MediaAsset:
    return MediaAsset(
        kind=kind,
        filename="lecture.m4a",
        mime="audio/mp4",
        storage_key=f"{org_id}/media/{uuid.uuid4()}/lecture.m4a",
        status="ready",
    )


def test_media_transcript_registered():
    """The model imports and is bound to Base.metadata."""
    assert "media_transcripts" in set(Base.metadata.tables)


async def test_media_transcript_round_trip(db_session: AsyncSession):
    """Store a transcript for an asset and read it back (org stamped)."""
    asset = _asset(DEFAULT_ORG_ID)
    db_session.add(asset)
    await db_session.flush()

    tr = MediaTranscript(
        media_asset_id=asset.id,
        text="Welcome to the course. Today we cover agentic AI.",
        language="en",
        provider="whisper-1",
    )
    db_session.add(tr)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(MediaTranscript).where(MediaTranscript.id == tr.id)
        )
    ).scalar_one()
    assert row.media_asset_id == asset.id
    assert row.text.startswith("Welcome")
    assert row.language == "en"
    assert row.provider == "whisper-1"
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


async def test_media_transcript_unique_per_asset(db_session: AsyncSession):
    """A second transcript for the same asset violates the UNIQUE constraint."""
    from sqlalchemy.exc import IntegrityError

    asset = _asset(DEFAULT_ORG_ID)
    db_session.add(asset)
    await db_session.flush()

    db_session.add(
        MediaTranscript(media_asset_id=asset.id, text="first", provider="p")
    )
    await db_session.flush()
    db_session.add(
        MediaTranscript(media_asset_id=asset.id, text="second", provider="p")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_media_transcript_tenant_isolation():
    """A transcript written under org A is invisible under org B."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.commit()
        org_a_id, org_b_id = org_a.id, org_b.id

    async def _session(org_id: uuid.UUID) -> AsyncSession:
        sess = factory()
        await sess.execute(
            text("SELECT set_config('app.current_org', :org, false)"),
            {"org": str(org_id)},
        )
        return sess

    with use_org(org_a_id):
        session = await _session(org_a_id)
        try:
            asset = _asset(org_a_id)
            session.add(asset)
            await session.flush()
            tr = MediaTranscript(
                media_asset_id=asset.id, text="A secret", provider="whisper-1"
            )
            session.add(tr)
            await session.commit()
            tr_id = tr.id
        finally:
            await session.close()

    with use_org(org_b_id):
        session = await _session(org_b_id)
        try:
            count_b = await session.scalar(
                select(func.count())
                .select_from(MediaTranscript)
                .where(MediaTranscript.id == tr_id)
            )
            assert count_b == 0, "org B must not see org A's transcript"
        finally:
            await session.close()

    with use_org(org_a_id):
        session = await _session(org_a_id)
        try:
            count_a = await session.scalar(
                select(func.count())
                .select_from(MediaTranscript)
                .where(MediaTranscript.id == tr_id)
            )
            assert count_a == 1, "org A must see its own transcript"
        finally:
            await session.close()

    await engine.dispose()
