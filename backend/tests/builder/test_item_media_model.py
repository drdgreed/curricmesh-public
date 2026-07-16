"""Model + tenant-isolation tests for the ``DraftItemMedia`` link table (slice 2).

``DraftItemMedia`` associates an owned ``MediaAsset`` with a ``DraftItem`` so
the author can embed it in item content and publish can freeze the reference
into the immutable model. Like every other authoring table it is
``TenantScoped`` and joins the RLS regime.

Round-trip runs under the DEFAULT_ORG ``db_session`` fixture; the isolation
test owns its schema + two orgs (mirrors ``test_models_rls.py``).
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
from tests.conftest import DEFAULT_ORG_ID

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


def test_draft_item_media_registered():
    """The link model imports and is bound to Base.metadata."""
    from app.builder.models import DraftItemMedia  # noqa: F401

    assert "draft_item_media" in set(Base.metadata.tables)


async def test_draft_item_media_round_trip(db_session: AsyncSession):
    """Attach a MediaAsset to a DraftItem and read the link back."""
    from app.builder.models import DraftCourse, DraftItem, DraftItemMedia
    from app.models.enums import AssetKind
    from app.models.media import MediaAsset

    course = DraftCourse(title="Media course")
    db_session.add(course)
    await db_session.flush()

    item = DraftItem(
        draft_course_id=course.id, kind=AssetKind.lesson_plan, title="Lesson 1"
    )
    asset = MediaAsset(
        kind="video",
        filename="intro.mp4",
        mime="video/mp4",
        storage_key=f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/intro.mp4",
        status="ready",
    )
    db_session.add_all([item, asset])
    await db_session.flush()

    link = DraftItemMedia(
        draft_item_id=item.id, media_asset_id=asset.id, order_index=2
    )
    db_session.add(link)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(DraftItemMedia).where(DraftItemMedia.id == link.id)
        )
    ).scalar_one()
    assert row.draft_item_id == item.id
    assert row.media_asset_id == asset.id
    assert row.order_index == 2
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


async def test_draft_item_media_unique_pair(db_session: AsyncSession):
    """The (draft_item_id, media_asset_id) pair is unique."""
    from sqlalchemy.exc import IntegrityError

    from app.builder.models import DraftCourse, DraftItem, DraftItemMedia
    from app.models.enums import AssetKind
    from app.models.media import MediaAsset

    course = DraftCourse(title="Dup course")
    db_session.add(course)
    await db_session.flush()
    item = DraftItem(
        draft_course_id=course.id, kind=AssetKind.lesson_plan, title="L"
    )
    asset = MediaAsset(
        kind="image",
        filename="a.png",
        mime="image/png",
        storage_key=f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/a.png",
        status="ready",
    )
    db_session.add_all([item, asset])
    await db_session.flush()

    db_session.add(DraftItemMedia(draft_item_id=item.id, media_asset_id=asset.id))
    await db_session.flush()
    db_session.add(DraftItemMedia(draft_item_id=item.id, media_asset_id=asset.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_draft_item_media_tenant_isolation():
    """A link written under org A is invisible under org B."""
    from app.builder.models import DraftCourse, DraftItem, DraftItemMedia
    from app.models.enums import AssetKind
    from app.models.media import MediaAsset

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
            course = DraftCourse(title="A course")
            session.add(course)
            await session.flush()
            item = DraftItem(
                draft_course_id=course.id, kind=AssetKind.lesson_plan, title="I"
            )
            asset = MediaAsset(
                kind="video",
                filename="v.mp4",
                mime="video/mp4",
                storage_key=f"{org_a_id}/media/{uuid.uuid4()}/v.mp4",
                status="ready",
            )
            session.add_all([item, asset])
            await session.flush()
            link = DraftItemMedia(draft_item_id=item.id, media_asset_id=asset.id)
            session.add(link)
            await session.commit()
            link_id = link.id
        finally:
            await session.close()

    with use_org(org_b_id):
        session = await _session(org_b_id)
        try:
            count_b = await session.scalar(
                select(func.count())
                .select_from(DraftItemMedia)
                .where(DraftItemMedia.id == link_id)
            )
            assert count_b == 0, "org B must not see org A's media link"
        finally:
            await session.close()

    with use_org(org_a_id):
        session = await _session(org_a_id)
        try:
            count_a = await session.scalar(
                select(func.count())
                .select_from(DraftItemMedia)
                .where(DraftItemMedia.id == link_id)
            )
            assert count_a == 1, "org A must see its own media link"
        finally:
            await session.close()

    await engine.dispose()
