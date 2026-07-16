"""Slice 2, task 3 — media carry-through on publish (immutability).

Publishing a draft whose item references an owned media asset must freeze the
asset reference into the item's immutable ``ContentVersion.media_refs``. A later
draft change (new upload / re-attach) must NOT mutate the already-released
version — the release pins the exact assets it shipped with.

Fixture style mirrors ``tests/builder/test_compile.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.compile import publish_draft
from app.builder.models import DraftCourse, DraftItem, DraftItemMedia
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.content_model import ContentVersion
from app.models.enums import AssetKind
from app.models.media import MediaAsset
from app.models.org import Organization
from app.tenant import use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


@pytest.fixture
async def rls_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _org(engine) -> uuid.UUID:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Test Org")
        s.add(org)
        await s.commit()
        return org.id


async def _open_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _ready_asset(session: AsyncSession, org_id: uuid.UUID, filename: str) -> MediaAsset:
    asset = MediaAsset(
        kind="video",
        filename=filename,
        mime="video/mp4",
        storage_key=f"{org_id}/media/{uuid.uuid4()}/{filename}",
        status="ready",
    )
    session.add(asset)
    await session.flush()
    return asset


async def test_publish_pins_attached_media_and_is_immutable(rls_engine):
    org_id = await _org(rls_engine)

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            asset = await _ready_asset(session, org_id, "lecture.mp4")
            course = DraftCourse(title="Media course", status="drafting")
            session.add(course)
            await session.flush()
            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.lesson_plan,
                title="Lesson 1",
                content=f"Watch this: ![[media:{asset.id}]]",
                week_index=1,
                order_index=0,
            )
            session.add(item)
            await session.flush()
            session.add(
                DraftItemMedia(
                    draft_item_id=item.id,
                    media_asset_id=asset.id,
                    order_index=0,
                )
            )
            await session.flush()

            version = (await publish_draft(session, course.id)).version
            await session.commit()
            version_id = version.id
            original_key = asset.storage_key
        finally:
            await session.close()

    # The item's ContentVersion pins the asset.
    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cv = (
                await session.execute(
                    select(ContentVersion).where(ContentVersion.media_refs.isnot(None))
                )
            ).scalar_one()
            assert cv.media_refs is not None
            assert len(cv.media_refs) == 1
            ref = cv.media_refs[0]
            assert ref["media_asset_id"] == str(asset.id)
            assert ref["storage_key"] == original_key
            assert ref["kind"] == "video"
            assert ref["filename"] == "lecture.mp4"
            assert ref["mime"] == "video/mp4"
            cv_id = cv.id
        finally:
            await session.close()

    # A later new upload + re-attach to the draft must NOT change the release.
    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            item = (
                await session.execute(select(DraftItem))
            ).scalars().first()
            new_asset = await _ready_asset(session, org_id, "replacement.mp4")
            session.add(
                DraftItemMedia(
                    draft_item_id=item.id,
                    media_asset_id=new_asset.id,
                    order_index=1,
                )
            )
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cv = (
                await session.execute(
                    select(ContentVersion).where(ContentVersion.id == cv_id)
                )
            ).scalar_one()
            # Still exactly the originally-shipped asset — the release is frozen.
            assert len(cv.media_refs) == 1
            assert cv.media_refs[0]["media_asset_id"] == str(asset.id)
            assert cv.media_refs[0]["storage_key"] == original_key
        finally:
            await session.close()
        # sanity: version we published is the one we inspected
        assert version_id is not None


async def test_publish_pins_inline_embed_without_explicit_attach(rls_engine):
    """An ``![[media:id]]`` embed with no DraftItemMedia link is still pinned."""
    org_id = await _org(rls_engine)
    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            asset = await _ready_asset(session, org_id, "inline.mp4")
            course = DraftCourse(title="Inline course", status="drafting")
            session.add(course)
            await session.flush()
            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.lesson_plan,
                title="Inline lesson",
                content=f"Intro ![[media:{asset.id}]] outro",
                week_index=1,
            )
            session.add(item)
            await session.flush()
            await publish_draft(session, course.id)
            await session.commit()
            asset_id = asset.id
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cv = (
                await session.execute(
                    select(ContentVersion).where(ContentVersion.media_refs.isnot(None))
                )
            ).scalar_one()
            assert cv.media_refs[0]["media_asset_id"] == str(asset_id)
        finally:
            await session.close()


async def test_publish_no_media_leaves_refs_null(rls_engine):
    """An item with no media references has ``media_refs`` NULL."""
    org_id = await _org(rls_engine)
    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            course = DraftCourse(title="Plain course", status="drafting")
            session.add(course)
            await session.flush()
            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.lesson_plan,
                title="Plain lesson",
                content="No media here.",
                week_index=1,
            )
            session.add(item)
            await session.flush()
            await publish_draft(session, course.id)
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            rows = (
                await session.execute(select(ContentVersion))
            ).scalars().all()
            assert rows, "expected at least one ContentVersion"
            assert all(cv.media_refs is None for cv in rows)
        finally:
            await session.close()
