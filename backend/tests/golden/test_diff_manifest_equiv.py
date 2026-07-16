"""M2 — version-diff read path ⇄ committed golden equivalence (immutable model).

Proves the diff port (``app/core/diff/service.diff_versions``) reads the
immutable content model and stays byte-for-byte equivalent to the legacy diff
the Task-G harness captured.

How it works
------------
After seeding + ``backfill_content_model``, every diffable asset has a
``ContentVersion`` chain, so ``get_asset_diff`` (router → ``diff_versions``)
transparently takes the **content-version** read path: it resolves each
requested ``AssetVersion`` to its immutable ``ContentVersion`` body (by
``content_hash``, exactly as the back-fill addressed content) and dispatches by
kind (rubric weights / LO id+text / unified text). This test re-runs the SAME
deterministic capture the golden harness uses (``capture.capture_asset_diff``,
which picks the same assets/semvers) and asserts the result ``assert_equivalent``
to the committed golden ``asset_diff`` fixture — both the structured rubric diff
and the text diff.

The legacy ``AssetVersion.body_ref`` fallback (when no chain exists) is covered
by the existing old-model diff tests (``tests/unit/test_diff.py`` /
``tests/integration/test_api_diff.py``), which keep passing through this port.

Per P-006, this seeds + back-fills WITHIN the test on its own dedicated engine
(``DATABASE_URL``) — it never depends on a pre-seeded shared DB.
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
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import ContentVersion
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.tenant import current_org
from seed.bootcamp_curriculum import seed
from tests.golden import capture
from tests.golden.capture import assert_equivalent

_ENUM_TYPES = ("lifecyclestatus", "assetkind")
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The seeded curricula whose diff goldens we assert equivalence against.
SEEDED_CURRICULA = ("agentic-ai", "cloud-data-eng")


@pytest.fixture
async def seeded_backfilled_engine():
    """A dedicated engine on a freshly-seeded **and back-filled** schema.

    Rebuilds the schema (drop+create), applies RLS, runs the real seed, then
    back-fills the immutable content model so every diffable asset has a
    ``ContentVersion`` chain (driving the new diff read path).
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


def _load_fixture(slug: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{slug}.json").read_text())


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    """Open a session pinned to ``org_id`` (GUC for RLS); caller closes it."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _org_for_slug(engine, slug: str) -> uuid.UUID | None:
    """Find the org owning ``slug`` by probing each org's tenant context."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_ids = [r[0] for r in (await s.execute(select(Organization.id))).all()]
    for oid in org_ids:
        token = current_org.set(oid)
        try:
            session = await _open_org_session(engine, oid)
            try:
                found = await session.scalar(
                    select(Curriculum.id).where(Curriculum.slug == slug)
                )
                if found is not None:
                    return oid
            finally:
                await session.rollback()
                await session.close()
        finally:
            current_org.reset(token)
    return None


@pytest.mark.parametrize("slug", SEEDED_CURRICULA)
async def test_diff_content_model_equals_golden(
    seeded_backfilled_engine, slug, capsys
):
    """The content-version diff == the committed golden diff for ``slug``.

    Asserts equivalence for BOTH captured diffs (the structured rubric diff and
    the text diff), and guards that the new read path was actually exercised
    (the asset has a back-filled ContentVersion chain, so this isn't trivially
    passing on the legacy fallback).
    """
    engine = seeded_backfilled_engine
    org_id = await _org_for_slug(engine, slug)
    assert org_id is not None, f"seeded curriculum {slug!r} not found"

    expected = _load_fixture(slug)["asset_diff"]

    token = current_org.set(org_id)
    try:
        session = await _open_org_session(engine, org_id)
        try:
            # Guard: the immutable chain exists (so we're on the NEW read path,
            # not the legacy fallback). Without a chain the equivalence would
            # still pass via fallback but wouldn't prove the port.
            cv_count = await session.scalar(
                select(ContentVersion.id).limit(1)
            )
            assert cv_count is not None, (
                f"{slug}: no ContentVersion back-filled — diff would fall back "
                "to the legacy path, not exercise the immutable read path"
            )

            actual = await capture.capture_asset_diff(session)
        finally:
            await session.rollback()
            await session.close()
    finally:
        current_org.reset(token)

    # Both slots must be present in the committed golden (text + structured).
    assert "representative" in expected and "representative" in actual
    assert "structured" in expected and "structured" in actual, (
        f"{slug}: golden/capture missing a structured diff slot"
    )

    # THE equivalence: the content-version diff matches the committed golden,
    # for the text diff and the structured rubric diff alike.
    assert_equivalent(expected, actual)

    with capsys.disabled():
        rep = actual["representative"]
        struct = actual["structured"]
        print(f"\n=== diff content-model equivalence [{slug}] ===")
        print(
            f"  text       : {rep['asset_key']} {rep['from_semver']}->"
            f"{rep['to_semver']} kind={rep['kind']} "
            f"(+{len(rep['text']['added'])}/-{len(rep['text']['removed'])})"
        )
        s = struct["structured"]
        print(
            f"  structured : {struct['asset_key']} {struct['from_semver']}->"
            f"{struct['to_semver']} kind={struct['kind']} "
            f"(+{len(s['added'])}/-{len(s['removed'])}/~{len(s['changed'])})"
        )
