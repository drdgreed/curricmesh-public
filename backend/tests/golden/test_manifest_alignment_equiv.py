"""M2.0 — manifest read layer ⇄ legacy read path equivalence (golden).

Proves the shared manifest service (``app/core/manifest.py``) reproduces today's
behavior on the seeded data, so the M2 graph/dashboard/diff/cascade ports that
consume it stay golden-equivalent:

  * **Alignment equivalence** — the manifest staleness set
    (``manifest_alignment``, keyed by ``LineageAsset.lineage_key``) equals the
    legacy staleness set (``alignment_report_for_version``, keyed by the
    transitive misaligned ``Asset.key``s the graph endpoint surfaces). Both are
    reduced to stable lineage keys and the **sets are asserted equal** — the
    transitive 8 per curriculum on the seed.
  * **Count equivalence** — ``version_members`` count == the in-version legacy
    asset count, and ``version_edges`` count == the in-version legacy edge count
    (both endpoints in-version), per the graph endpoint's predicate.

The legacy reads recreate the schema empty (the shared ``db_session`` fixture
wipes data), so per P-006's guidance this test **seeds + back-fills within the
test** on its own dedicated engine — it never depends on a pre-seeded DB.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.cascade.engine import alignment_report_for_version
from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment,
    version_edges,
    version_members,
)
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.graph import DependencyEdge
from app.models.org import Organization
from app.models.structure import Asset, Module, Project
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


@pytest.fixture
async def seeded_backfilled_engine():
    """A dedicated engine on a freshly-seeded **and back-filled** schema.

    Rebuilds the schema (drop+create), applies RLS, runs the real seed (both demo
    orgs / curricula), then back-fills the immutable content model. Yields the
    engine; the caller opens org-scoped sessions against it.
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

    # Seed runs with NO ambient tenant context (it self-scopes per org).
    async with session_factory() as session:
        await seed(session)
        await backfill_content_model(session)

    yield engine
    await engine.dispose()


def _seeded_org_ids_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _org_ids(engine) -> list[uuid.UUID]:
    factory = _seeded_org_ids_session_factory(engine)
    async with factory() as s:
        rows = await s.execute(select(Organization.id))
        return [r[0] for r in rows.all()]


async def _open_org_session(engine, org_id: uuid.UUID):
    """Open a session pinned to ``org_id`` (ContextVar + GUC), caller closes it."""
    factory = _seeded_org_ids_session_factory(engine)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Legacy side — recompute the in-version asset/edge counts + the misaligned set
# exactly as the graph endpoint does, keyed on the stable Asset.key.
# ---------------------------------------------------------------------------


async def _legacy_in_version_keys(
    session: AsyncSession, legacy_version_id: uuid.UUID
) -> set[str]:
    """The stable keys of assets present in a legacy version (graph predicate)."""
    module_ids = [
        r[0]
        for r in (
            await session.execute(
                select(Module.id).where(Module.version_id == legacy_version_id)
            )
        ).all()
    ]
    project_ids = [
        r[0]
        for r in (
            await session.execute(
                select(Project.id).where(Project.version_id == legacy_version_id)
            )
        ).all()
    ]
    if not module_ids and not project_ids:
        return set()
    assets = (
        (
            await session.execute(
                select(Asset).where(
                    (Asset.module_id.in_(module_ids))
                    | (Asset.project_id.in_(project_ids))
                )
            )
        )
        .scalars()
        .all()
    )
    return {a.key for a in assets}


async def _legacy_in_version_edge_count(
    session: AsyncSession, in_version_asset_ids: set[uuid.UUID]
) -> int:
    """Edges with BOTH endpoints in-version (the graph endpoint's edge predicate)."""
    if not in_version_asset_ids:
        return 0
    edges = (
        (
            await session.execute(
                select(DependencyEdge).where(
                    DependencyEdge.from_asset_id.in_(in_version_asset_ids),
                    DependencyEdge.to_asset_id.in_(in_version_asset_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(edges)


async def _legacy_misaligned_keys(
    session: AsyncSession, legacy_version_id: uuid.UUID
) -> set[str]:
    """The graph endpoint's ``misaligned_asset_ids`` reduced to stable Asset.keys."""
    misalignments = await alignment_report_for_version(session, legacy_version_id)
    misaligned_ids = {m.dependent_asset_id for m in misalignments}
    if not misaligned_ids:
        return set()
    rows = await session.execute(
        select(Asset.id, Asset.key).where(Asset.id.in_(misaligned_ids))
    )
    id_to_key = {r[0]: r[1] for r in rows.all()}
    return {id_to_key[i] for i in misaligned_ids if i in id_to_key}


# ---------------------------------------------------------------------------
# The equivalence test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expect_misaligned_count", [8])
async def test_manifest_alignment_equals_legacy(
    seeded_backfilled_engine, expect_misaligned_count, capsys
):
    """manifest_alignment == legacy alignment (lineage-key sets) per curriculum.

    Also asserts member/edge counts match the in-version legacy counts, and that
    the seed's misaligned set is the expected size (a regression guard so the
    equivalence isn't trivially true on an empty set).
    """
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    assert org_ids, "seed created no organizations"

    checked_curricula = 0
    report_lines: list[str] = []

    for org_id in org_ids:
        token = current_org.set(org_id)
        try:
            with use_org(org_id):
                session = await _open_org_session(engine, org_id)
                try:
                    curricula = (
                        (await session.execute(select(Curriculum))).scalars().all()
                    )
                    for curriculum in curricula:
                        legacy_version_id = curriculum.current_version_id
                        assert legacy_version_id is not None, (
                            f"curriculum {curriculum.slug} has no active version"
                        )

                        # --- LEGACY side ---
                        legacy_in_version_keys = await _legacy_in_version_keys(
                            session, legacy_version_id
                        )
                        legacy_assets = (
                            (
                                await session.execute(
                                    select(Asset).where(
                                        Asset.key.in_(legacy_in_version_keys)
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        legacy_in_version_asset_ids = {a.id for a in legacy_assets}
                        legacy_edge_count = await _legacy_in_version_edge_count(
                            session, legacy_in_version_asset_ids
                        )
                        legacy_misaligned = await _legacy_misaligned_keys(
                            session, legacy_version_id
                        )

                        # --- MANIFEST side ---
                        cversion = await active_curriculum_version(
                            session, curriculum.id
                        )
                        assert cversion is not None, (
                            f"no active CurriculumVersion resolved for "
                            f"{curriculum.slug}"
                        )
                        members = await version_members(session, cversion.id)
                        edges = await version_edges(session, cversion.id)
                        manifest_ids = await manifest_alignment(session, cversion.id)

                        # lineage_key for every member, to translate ids -> keys
                        key_by_asset = {m.asset_id: m.lineage_key for m in members}
                        manifest_misaligned = {
                            key_by_asset[i]
                            for i in manifest_ids
                            if i in key_by_asset
                        }

                        # --- ASSERTIONS ---
                        # member count == in-version legacy asset count
                        assert len(members) == len(legacy_in_version_keys), (
                            f"{curriculum.slug}: members={len(members)} != "
                            f"legacy in-version assets={len(legacy_in_version_keys)}"
                        )
                        # edge count == in-version legacy edge count
                        assert len(edges) == legacy_edge_count, (
                            f"{curriculum.slug}: manifest edges={len(edges)} != "
                            f"legacy in-version edges={legacy_edge_count}"
                        )
                        # every manifest id resolved to a known lineage key
                        assert len(manifest_misaligned) == len(manifest_ids), (
                            f"{curriculum.slug}: {len(manifest_ids) - len(manifest_misaligned)} "
                            "misaligned manifest id(s) not in this version's members"
                        )
                        # THE equivalence: the lineage-key sets are equal
                        assert manifest_misaligned == legacy_misaligned, (
                            f"{curriculum.slug}: manifest alignment != legacy\n"
                            f"  only-in-manifest: {sorted(manifest_misaligned - legacy_misaligned)}\n"
                            f"  only-in-legacy:   {sorted(legacy_misaligned - manifest_misaligned)}"
                        )
                        # regression guard: the seed is non-trivially misaligned
                        assert len(legacy_misaligned) == expect_misaligned_count, (
                            f"{curriculum.slug}: expected "
                            f"{expect_misaligned_count} misaligned, got "
                            f"{len(legacy_misaligned)}"
                        )

                        report_lines.append(
                            f"[{curriculum.slug}] members={len(members)} "
                            f"edges={len(edges)} "
                            f"misaligned(manifest==legacy)="
                            f"{len(manifest_misaligned)} : "
                            f"{sorted(manifest_misaligned)}"
                        )
                        checked_curricula += 1
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token)

    assert checked_curricula >= 2, (
        f"expected to check both seeded curricula, checked {checked_curricula}"
    )

    with capsys.disabled():
        print("\n=== manifest-vs-legacy alignment equivalence ===")
        for line in report_lines:
            print(line)
