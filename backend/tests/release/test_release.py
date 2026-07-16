"""Phase C — executable release endpoint (fork() wired into HTTP).

Drives the real ``app.routers.releases.create_release`` handler against a
freshly-seeded + back-filled schema (manifest live), mirroring the golden/course
org-pinned fixture pattern (P-006: seed + back-fill within the test on a dedicated
engine).

What is asserted:
  * add — a release that adds an asset bumps semver, activates a new version
    (member_count = parent + 1), and the change is visible through the graph
    endpoint (the new lineage node appears).
  * change — a release that edits one asset's content writes exactly ONE new
    ContentVersion (structural sharing: the others are referenced, not copied).
  * validation — a release whose edge references a non-member rolls back as 422.
  * concurrency — a stale ``expected_active_id`` is rejected as 409, nothing
    persisted.
  * 404 — releasing an unknown curriculum.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import ContentVersion
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.routers.graph import get_curriculum_graph
from app.routers.releases import create_release
from app.schemas.release import (
    ContentEditIn,
    EdgeSpecIn,
    NewAssetIn,
    ReleaseRequest,
)
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

# The role gate is the Depends(_RELEASE_ROLES) wrapper, which the harness bypasses
# by calling the handler directly; the body only reads current["sub"].
_USER: dict[str, Any] = {"sub": str(uuid.uuid4()), "role": "architect"}

SEEDED_SLUG = "agentic-ai"


@pytest.fixture
async def seeded_backfilled_engine():
    """Dedicated engine on a freshly-seeded + back-filled schema (manifest live)."""
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


async def _first_org(engine) -> uuid.UUID:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(Organization.id))).scalars().first()


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _curriculum(session: AsyncSession) -> Curriculum:
    cur = await session.scalar(select(Curriculum).where(Curriculum.slug == SEEDED_SLUG))
    assert cur is not None, "seed missing the agentic-ai curriculum"
    return cur


async def _count_content_versions(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(ContentVersion))


async def _real_user(session: AsyncSession) -> dict[str, Any]:
    """A current-user dict backed by a real seeded user (history FK is satisfied)."""
    uid = (await session.execute(select(User.id))).scalars().first()
    assert uid is not None, "seed created no users"
    return {"sub": str(uid), "role": "architect"}


async def _active_member_count(session: AsyncSession, curriculum_id: uuid.UUID) -> int:
    cv = await active_curriculum_version(session, curriculum_id)
    assert cv is not None
    return len(await version_members(session, cv.id))


# ---------------------------------------------------------------------------


async def test_release_add_asset_bumps_and_is_visible(seeded_backfilled_engine):
    """Adding an asset activates a new minor version visible in the graph."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                user = await _real_user(session)
                before_members = await _active_member_count(session, cur.id)
                before_graph = await get_curriculum_graph(
                    cur.id, current=user, db=session
                )

                body = ReleaseRequest(
                    bump="minor",
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/99/bonus_lab",
                            kind=AssetKind.lab,
                            content="# Bonus capstone lab\nAdded via executable release.",
                            section="Week 99: Bonus",
                            week_index=99,
                            order=0,
                            source_url="https://example.com/bonus",
                        )
                    ],
                )
                out = await create_release(cur.id, body, current=user, db=session)

                assert out.member_count == before_members + 1
                assert out.summary.added == 1
                assert out.status == "active"
                # minor bump from the seeded active version
                assert out.semver.split(".")[2] == "0" and out.semver != "0.0.0"
                assert out.parent_version_id is not None

                # The new version is now active and the graph shows the new node.
                after_graph = await get_curriculum_graph(
                    cur.id, current=user, db=session
                )
                assert len(after_graph.nodes) == len(before_graph.nodes) + 1
                labels = {n.label for n in after_graph.nodes}
                assert "agentic-ai/v1/99/bonus_lab" in labels
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_release_change_is_structural_sharing(seeded_backfilled_engine):
    """Editing one asset writes exactly one new ContentVersion (sharing)."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                user = await _real_user(session)
                cv = await active_curriculum_version(session, cur.id)
                members = await version_members(session, cv.id)
                target_key = members[0].lineage_key

                before = await _count_content_versions(session)
                body = ReleaseRequest(
                    bump="patch",
                    changed=[
                        ContentEditIn(
                            lineage_key=target_key,
                            content="Edited content — a single new revision.",
                        )
                    ],
                )
                out = await create_release(cur.id, body, current=user, db=session)
                after = await _count_content_versions(session)

                # Exactly one new immutable content row; everything else shared.
                assert after - before == 1
                assert out.summary.changed == 1
                assert out.member_count == len(members)  # no add/remove
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_release_dangling_edge_is_422(seeded_backfilled_engine):
    """An edge referencing a non-member rolls back as 422 (referential validity)."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                cv = await active_curriculum_version(session, cur.id)
                members = await version_members(session, cv.id)
                real_key = members[0].lineage_key
                before = await _count_content_versions(session)

                body = ReleaseRequest(
                    bump="patch",
                    edges_added=[
                        EdgeSpecIn(
                            from_key="does/not/exist",
                            to_key=real_key,
                        )
                    ],
                )
                with pytest.raises(HTTPException) as exc:
                    await create_release(cur.id, body, current=_USER, db=session)
                assert exc.value.status_code == 422

                # Fail-closed: nothing persisted.
                await session.rollback()
                assert await _count_content_versions(session) == before
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_release_stale_expected_active_is_409(seeded_backfilled_engine):
    """A stale optimistic-concurrency token is rejected as 409."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                body = ReleaseRequest(
                    bump="patch",
                    changed=[],
                    expected_active_id=uuid.uuid4(),  # never the real active id
                )
                with pytest.raises(HTTPException) as exc:
                    await create_release(cur.id, body, current=_USER, db=session)
                assert exc.value.status_code == 409
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_release_unknown_curriculum_is_404(seeded_backfilled_engine):
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                body = ReleaseRequest(bump="patch")
                with pytest.raises(HTTPException) as exc:
                    await create_release(
                        uuid.uuid4(), body, current=_USER, db=session
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
