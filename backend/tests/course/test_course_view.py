"""Feature A — course-content browser: calendar, asset detail, source-url PATCH.

Exercises the real router handlers against a freshly-seeded **and back-filled**
schema (the immutable manifest is populated), mirroring the golden tests'
org-pinned fixture pattern (P-006: seed + back-fill within the test on a dedicated
engine, never a pre-seeded DB).

What is asserted:
  * calendar — non-empty, week-ordered sections; tiles ordered by ``order`` within
    a section; every tile id is a real legacy ``Asset.id``; tile count reconciles
    with the version's member count; source_url/kind/label populated; the
    ``misaligned`` flag matches ``manifest_alignment``.
  * asset detail — selected content matches the active member's ContentVersion; a
    seq-ordered history chain; prerequisites/dependents match the version's edges
    (mapped to legacy ids).
  * PATCH source-url — persists, is reflected by a follow-up calendar tile + asset
    detail, and round-trips a ``None`` clear.
  * tenant isolation — org B sees nothing of org A's curriculum.
  * 404s — unknown curriculum / unknown asset id.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment,
    version_edges,
    version_members,
)
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.models.structure import Asset
from app.routers.course import (
    get_asset_detail,
    get_course_calendar,
    patch_asset_source_url,
)
from app.schemas.course import SourceUrlIn
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

# Router handlers enforce auth via the Depends wrapper, which the harness bypasses
# (it is already inside tenant context). The body never reads the claim payload
# except the PATCH role guard, which we exercise by calling the handler directly.
_STUB_USER: dict[str, Any] = {}

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


async def _org_ids(engine) -> list[uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return [r[0] for r in (await s.execute(select(Organization.id))).all()]


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    """Open a session pinned to ``org_id`` (ContextVar set by caller + GUC here)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _find_curriculum(session: AsyncSession, slug: str) -> Curriculum | None:
    return await session.scalar(select(Curriculum).where(Curriculum.slug == slug))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


async def test_calendar_sections_ordered_and_legacy_ids(seeded_backfilled_engine):
    """Calendar returns ordered sections of tiles keyed on real legacy Asset ids."""
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    assert org_ids

    found = False
    for org_id in org_ids:
        token = current_org.set(org_id)
        try:
            with use_org(org_id):
                session = await _open_org_session(engine, org_id)
                try:
                    curriculum = await _find_curriculum(session, SEEDED_SLUG)
                    if curriculum is None:
                        continue
                    found = True

                    out = await get_course_calendar(
                        curriculum.id, current=_STUB_USER, db=session
                    )

                    # Non-empty sections.
                    assert out.sections, "expected non-empty calendar sections"

                    # Sections ordered by week_index (non-decreasing).
                    weeks = [s.week_index for s in out.sections]
                    assert weeks == sorted(weeks), "sections not week-ordered"

                    # Tiles within each section ordered by `order`. We resolve the
                    # member order via the manifest to compare.
                    cversion = await active_curriculum_version(session, curriculum.id)
                    members = await version_members(session, cversion.id)
                    member_order = {
                        m.lineage_key: (m.week_index, m.section, m.order)
                        for m in members
                    }

                    # All assets table ids (to prove tile ids are legacy Asset ids).
                    asset_ids = {
                        a.id
                        for a in (
                            await session.execute(select(Asset))
                        ).scalars().all()
                    }

                    total_tiles = 0
                    for s in out.sections:
                        total_tiles += len(s.tiles)
                        orders = [
                            member_order[t.lineage_key][2] for t in s.tiles
                        ]
                        assert orders == sorted(orders), (
                            f"tiles in week {s.week_index}/{s.section} not order-sorted"
                        )
                        for t in s.tiles:
                            assert t.id in asset_ids, (
                                f"tile id {t.id} is not a legacy Asset id"
                            )
                            assert t.kind is not None
                            assert t.label
                            assert t.lineage_key

                    # Tile count reconciles with the version's member count.
                    assert total_tiles == len(members), (
                        f"tiles={total_tiles} != members={len(members)}"
                    )

                    # misaligned flag matches manifest_alignment (on lineage ids,
                    # mapped to our tile lineage keys).
                    misaligned_lineage = await manifest_alignment(
                        session, cversion.id
                    )
                    lineage_key_by_id = {
                        la.id: la.lineage_key
                        for la in (
                            await session.execute(select(LineageAsset))
                        ).scalars().all()
                    }
                    misaligned_keys = {
                        lineage_key_by_id[i] for i in misaligned_lineage
                    }
                    for s in out.sections:
                        for t in s.tiles:
                            assert t.misaligned == (
                                t.lineage_key in misaligned_keys
                            ), f"misaligned mismatch for {t.lineage_key}"
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token)

    assert found, f"seeded curriculum {SEEDED_SLUG!r} not found in any org"


async def test_calendar_source_url_populated(seeded_backfilled_engine):
    """A tile's source_url reflects its LineageAsset.source_url (seed may be None)."""
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    for org_id in org_ids:
        token = current_org.set(org_id)
        try:
            with use_org(org_id):
                session = await _open_org_session(engine, org_id)
                try:
                    curriculum = await _find_curriculum(session, SEEDED_SLUG)
                    if curriculum is None:
                        continue
                    out = await get_course_calendar(
                        curriculum.id, current=_STUB_USER, db=session
                    )
                    # source_url is a str|None per LineageAsset; assert type only.
                    for s in out.sections:
                        for t in s.tiles:
                            assert t.source_url is None or isinstance(
                                t.source_url, str
                            )
                    return
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token)


async def test_calendar_unknown_curriculum_404(seeded_backfilled_engine):
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    org_id = org_ids[0]
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                with pytest.raises(Exception) as exc:
                    await get_course_calendar(
                        uuid.uuid4(), current=_STUB_USER, db=session
                    )
                assert getattr(exc.value, "status_code", None) == 404
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# Asset detail
# ---------------------------------------------------------------------------


async def test_asset_detail_content_history_and_relations(seeded_backfilled_engine):
    """Asset detail returns the selected content, seq-ordered history, and the
    version's edges mapped to legacy ids."""
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)

    for org_id in org_ids:
        token = current_org.set(org_id)
        try:
            with use_org(org_id):
                session = await _open_org_session(engine, org_id)
                try:
                    curriculum = await _find_curriculum(session, SEEDED_SLUG)
                    if curriculum is None:
                        continue

                    cversion = await active_curriculum_version(session, curriculum.id)
                    members = await version_members(session, cversion.id)
                    edges = await version_edges(session, cversion.id)

                    # Pick a member that has at least one edge so we exercise
                    # prerequisites/dependents.
                    edge_lineage_ids = {e.from_asset_id for e in edges} | {
                        e.to_asset_id for e in edges
                    }
                    target = next(
                        (m for m in members if m.asset_id in edge_lineage_ids),
                        members[0],
                    )

                    # Map lineage -> legacy Asset id (shared key).
                    legacy_asset = await session.scalar(
                        select(Asset).where(Asset.key == target.lineage_key)
                    )
                    assert legacy_asset is not None

                    detail = await get_asset_detail(
                        legacy_asset.id, current=_STUB_USER, db=session
                    )

                    assert detail.id == legacy_asset.id
                    assert detail.lineage_key == target.lineage_key

                    # Selected content matches the active member's ContentVersion.
                    selected = await session.scalar(
                        select(ContentVersion).where(
                            ContentVersion.id == target.content_version_id
                        )
                    )
                    assert detail.content == selected.content
                    assert detail.content_seq == selected.seq
                    assert detail.content_hash == selected.content_hash

                    # History chain ordered by seq ascending, covering all
                    # ContentVersions of the lineage.
                    lineage = await session.scalar(
                        select(LineageAsset).where(
                            LineageAsset.lineage_key == target.lineage_key
                        )
                    )
                    all_cvs = (
                        (
                            await session.execute(
                                select(ContentVersion).where(
                                    ContentVersion.asset_id == lineage.id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(detail.version_history) == len(all_cvs)
                    seqs = [v.seq for v in detail.version_history]
                    assert seqs == sorted(seqs), "history not seq-ordered"

                    # Prerequisites = incoming edges (to == target);
                    # dependents = outgoing edges (from == target). Compare on the
                    # related lineage keys mapped to legacy ids.
                    lineage_key_by_id = {
                        la.id: la.lineage_key
                        for la in (
                            await session.execute(select(LineageAsset))
                        ).scalars().all()
                    }
                    expected_prereq_keys = {
                        lineage_key_by_id[e.from_asset_id]
                        for e in edges
                        if e.to_asset_id == lineage.id
                    }
                    expected_dependent_keys = {
                        lineage_key_by_id[e.to_asset_id]
                        for e in edges
                        if e.from_asset_id == lineage.id
                    }
                    assert {
                        p.lineage_key for p in detail.prerequisites
                    } == expected_prereq_keys
                    assert {
                        d.lineage_key for d in detail.dependents
                    } == expected_dependent_keys

                    # Related refs carry legacy ids that exist in `assets`.
                    asset_ids = {
                        a.id
                        for a in (
                            await session.execute(select(Asset))
                        ).scalars().all()
                    }
                    for ref in detail.prerequisites + detail.dependents:
                        assert ref.id in asset_ids
                        assert ref.label
                        assert ref.edge_type
                    return
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token)


async def test_asset_detail_unknown_id_404(seeded_backfilled_engine):
    engine = seeded_backfilled_engine
    org_id = (await _org_ids(engine))[0]
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                with pytest.raises(Exception) as exc:
                    await get_asset_detail(
                        uuid.uuid4(), current=_STUB_USER, db=session
                    )
                assert getattr(exc.value, "status_code", None) == 404
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# PATCH source-url
# ---------------------------------------------------------------------------


async def test_patch_source_url_persists_and_clears(seeded_backfilled_engine):
    """Setting a source_url persists + reflects in calendar/detail; None clears."""
    engine = seeded_backfilled_engine
    org_id = (await _org_ids(engine))[0]
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                curriculum = await _find_curriculum(session, SEEDED_SLUG)
                assert curriculum is not None
                cversion = await active_curriculum_version(session, curriculum.id)
                members = await version_members(session, cversion.id)
                target = members[0]
                legacy_asset = await session.scalar(
                    select(Asset).where(Asset.key == target.lineage_key)
                )
                assert legacy_asset is not None

                new_url = "https://docs.example.com/edit/source"
                res = await patch_asset_source_url(
                    legacy_asset.id,
                    SourceUrlIn(source_url=new_url),
                    current=_STUB_USER,
                    db=session,
                )
                assert res.source_url == new_url
                assert res.id == legacy_asset.id

                # Reflected in asset detail.
                detail = await get_asset_detail(
                    legacy_asset.id, current=_STUB_USER, db=session
                )
                assert detail.source_url == new_url

                # Reflected in the calendar tile.
                calendar = await get_course_calendar(
                    curriculum.id, current=_STUB_USER, db=session
                )
                tile = next(
                    t
                    for s in calendar.sections
                    for t in s.tiles
                    if t.lineage_key == target.lineage_key
                )
                assert tile.source_url == new_url

                # Clear it back to None.
                cleared = await patch_asset_source_url(
                    legacy_asset.id,
                    SourceUrlIn(source_url=None),
                    current=_STUB_USER,
                    db=session,
                )
                assert cleared.source_url is None
                detail2 = await get_asset_detail(
                    legacy_asset.id, current=_STUB_USER, db=session
                )
                assert detail2.source_url is None
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token)


async def test_patch_source_url_unknown_id_404(seeded_backfilled_engine):
    engine = seeded_backfilled_engine
    org_id = (await _org_ids(engine))[0]
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                with pytest.raises(Exception) as exc:
                    await patch_asset_source_url(
                        uuid.uuid4(),
                        SourceUrlIn(source_url="https://x"),
                        current=_STUB_USER,
                        db=session,
                    )
                assert getattr(exc.value, "status_code", None) == 404
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_tenant_isolation(seeded_backfilled_engine):
    """Org B cannot see org A's curriculum calendar or its assets.

    We resolve org A's curriculum + one of its assets, then re-query as org B:
    the curriculum is invisible (404) and the asset id is invisible (404), proving
    RLS/scoping holds for both read endpoints.
    """
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    assert len(org_ids) >= 2, "need two orgs for isolation test"
    org_a, org_b = org_ids[0], org_ids[1]

    # As org A: capture a curriculum that exists in A and a legacy asset id in it.
    token_a = current_org.set(org_a)
    a_curriculum_id: uuid.UUID | None = None
    a_asset_id: uuid.UUID | None = None
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                curriculum = await _find_curriculum(session, SEEDED_SLUG)
                if curriculum is not None:
                    a_curriculum_id = curriculum.id
                    cversion = await active_curriculum_version(
                        session, curriculum.id
                    )
                    members = await version_members(session, cversion.id)
                    legacy_asset = await session.scalar(
                        select(Asset).where(Asset.key == members[0].lineage_key)
                    )
                    a_asset_id = legacy_asset.id if legacy_asset else None
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token_a)

    if a_curriculum_id is None:
        # Curriculum lives in org B's tenant — swap the roles so the test still
        # asserts isolation from the other direction.
        org_a, org_b = org_b, org_a
        token_a = current_org.set(org_a)
        try:
            with use_org(org_a):
                session = await _open_org_session(engine, org_a)
                try:
                    curriculum = await _find_curriculum(session, SEEDED_SLUG)
                    assert curriculum is not None
                    a_curriculum_id = curriculum.id
                    cversion = await active_curriculum_version(
                        session, curriculum.id
                    )
                    members = await version_members(session, cversion.id)
                    legacy_asset = await session.scalar(
                        select(Asset).where(Asset.key == members[0].lineage_key)
                    )
                    a_asset_id = legacy_asset.id if legacy_asset else None
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token_a)

    assert a_curriculum_id is not None and a_asset_id is not None

    # As org B: the calendar 404s and the asset 404s (invisible under RLS).
    token_b = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(Exception) as exc1:
                    await get_course_calendar(
                        a_curriculum_id, current=_STUB_USER, db=session
                    )
                assert getattr(exc1.value, "status_code", None) == 404

                with pytest.raises(Exception) as exc2:
                    await get_asset_detail(
                        a_asset_id, current=_STUB_USER, db=session
                    )
                assert getattr(exc2.value, "status_code", None) == 404
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token_b)


# ---------------------------------------------------------------------------
# Source-url scheme allowlist (stored-XSS defense in depth) — pure schema test
# ---------------------------------------------------------------------------


def test_source_url_rejects_dangerous_scheme():
    """SourceUrlIn rejects javascript:/data: and accepts http(s) / clears empty."""
    import pytest as _pytest
    from pydantic import ValidationError

    from app.schemas.course import SourceUrlIn

    # Dangerous schemes are rejected (422 at the API layer).
    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x"):
        with _pytest.raises(ValidationError):
            SourceUrlIn(source_url=bad)

    # http(s) accepted; None / blank normalize to None (a clear).
    assert SourceUrlIn(source_url="https://example.com/x").source_url == "https://example.com/x"
    assert SourceUrlIn(source_url="http://example.com").source_url == "http://example.com"
    assert SourceUrlIn(source_url=None).source_url is None
    assert SourceUrlIn(source_url="   ").source_url is None
