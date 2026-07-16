"""M2.2 — dashboard alignment (manifest read path) ⇄ committed golden (equiv).

Proves the dashboard's *manifest-based* alignment port (``routers/dashboard.py``
→ ``_manifest_alignment_entries``, fed by ``app/core/manifest.py``) reproduces
the committed baseline for both seeded curricula. When a curriculum has a
populated manifest (which a back-fill produces), ``get_dashboard`` builds its
alignment entries from the manifest; this test seeds + back-fills its OWN DB so
that path is exercised, captures the dashboard alignment with the *same*
normalizer the golden was built with (:func:`capture.capture_dashboard_alignment`),
and asserts it ``assert_equivalent`` to the committed fixtures' ``dashboard_alignment``
section — both the friendly **names** and the **stale (dependent, dependency)
relationships**.

Why self-seed (not the shared ``require_seeded`` fixture): the shared local DB is
owned by another agent, and per P-006 a manifest test must not depend on a
pre-seeded DB. So this test stands up a dedicated engine, rebuilds the schema,
seeds both demo orgs, and back-fills the immutable content model in-test — the
same pattern as ``test_manifest_alignment_equiv.py``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.manifest import active_curriculum_version
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed
from tests.golden import capture
from tests.golden.capture import assert_equivalent
from tests.golden.conftest import SEEDED_CURRICULA

_ENUM_TYPES = ("lifecyclestatus", "assetkind")
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_golden(slug: str) -> dict:
    return json.loads((_FIXTURES_DIR / f"{slug}.json").read_text())


@pytest.fixture
async def seeded_backfilled_engine():
    """A dedicated engine on a freshly-seeded **and back-filled** schema.

    Rebuilds the schema (drop+create), applies RLS, runs the real seed (both demo
    orgs / curricula), then back-fills the immutable content model so the
    dashboard's manifest read path is the one under test. Yields the engine.
    """
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


async def _org_id_for_slug(engine, slug: str) -> uuid.UUID:
    """Resolve the org owning ``slug`` by probing each org's tenant context."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_ids = [r[0] for r in (await s.execute(select(Organization.id))).all()]
    for oid in org_ids:
        token = current_org.set(oid)
        try:
            with use_org(oid):
                s = factory()
                await s.execute(
                    text("SELECT set_config('app.current_org', :o, false)"),
                    {"o": str(oid)},
                )
                try:
                    found = await s.execute(
                        select(Curriculum.id).where(Curriculum.slug == slug)
                    )
                    if found.scalar_one_or_none() is not None:
                        return oid
                finally:
                    await s.rollback()
                    await s.close()
        finally:
            current_org.reset(token)
    raise AssertionError(f"no org owns curriculum {slug!r} after seed")


@pytest.mark.parametrize("slug", sorted(SEEDED_CURRICULA))
async def test_dashboard_alignment_manifest_equals_golden(
    seeded_backfilled_engine, slug
):
    """get_dashboard's MANIFEST alignment == the committed golden for ``slug``.

    Confirms the manifest read path is actually exercised (a populated
    ``CurriculumVersion`` resolves), then asserts the captured dashboard-alignment
    section is golden-equivalent — names + stale relationships — to the fixture.
    """
    engine = seeded_backfilled_engine
    org_id = await _org_id_for_slug(engine, slug)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    golden_alignment = _load_golden(slug)["dashboard_alignment"]

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = factory()
            await session.execute(
                text("SELECT set_config('app.current_org', :o, false)"),
                {"o": str(org_id)},
            )
            try:
                # Guard: the curriculum really is on the manifest path, so this
                # test can't pass trivially via the legacy fallback.
                curriculum_id = (
                    await session.execute(
                        select(Curriculum.id).where(Curriculum.slug == slug)
                    )
                ).scalar_one()
                cversion = await active_curriculum_version(session, curriculum_id)
                assert cversion is not None, (
                    f"{slug}: no active CurriculumVersion — manifest path not "
                    "exercised (test would be trivially on the legacy fallback)"
                )

                # Capture via the SAME normalizer the golden was built with.
                captured = await capture.capture_dashboard_alignment(session)
            finally:
                await session.rollback()
                await session.close()
    finally:
        current_org.reset(token)

    # At seed time each tenant owns exactly this one curriculum, so the captured
    # whole-tenant dashboard alignment is keyed by exactly {slug}.
    assert set(captured) == {slug}, (
        f"expected dashboard alignment keyed by just {slug!r}, got {sorted(captured)}"
    )
    # Regression guard: the seed's alignment is non-trivially populated (8 stale
    # pairs per curriculum), so equivalence isn't vacuously true on an empty list.
    assert len(captured[slug]["alignment"]) == 8, (
        f"{slug}: expected 8 stale pairs, got "
        f"{len(captured[slug]['alignment'])}"
    )

    assert_equivalent(golden_alignment, captured)
