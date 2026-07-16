"""Integration tests for the immutable-model back-fill (Task 1.2).

Against a freshly-seeded DB (both demo orgs via ``seed.bootcamp_curriculum``),
run :func:`app.migration.backfill_content_model.backfill_content_model` and assert
the four properties the cutover depends on:

* **Counts reconcile** — per curriculum-version, ``version_members`` ==
  in-version assets under the legacy model and ``version_edges`` == in-version
  edges; one ``CurriculumVersion`` per legacy ``Version``.
* **Content-hash integrity** — every ``ContentVersion.content_hash`` equals
  ``content_hash(kind, content, metadata)`` recomputed (the ``fsck`` pass).
* **Structural sharing / dedup** — the seed only populates the *active*
  version's structure (the archived 0.9.0 has no modules/assets), so sharing of
  one logical asset's content across two curriculum-versions isn't exercised by
  the seed (asserted), and the dedup that *enables* sharing is unit-tested
  directly: two identical legacy contents collapse to one ``ContentVersion`` row.
* **Idempotency** — running the back-fill twice creates no new rows and yields
  identical aggregate counts.

These reuse the standard ``db_session`` fixture (fresh empty schema per test);
each test seeds the demo data itself, then back-fills. The back-fill discovers
both orgs and runs each inside its own tenant context, so we read results back
per org via ``use_org``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.migration.backfill_content_model import (
    _backfill_content_versions,
    _get_or_create_lineage_asset,
    backfill_content_model,
)
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.graph import DependencyEdge
from app.models.org import Organization
from app.models.structure import Asset, AssetVersion, Module, Project
from app.models.version import Version
from app.tenant import use_org
from seed.bootcamp_curriculum import seed


async def _seed_both_orgs(db_session: AsyncSession) -> list[uuid.UUID]:
    """Seed both demo orgs and return their org ids.

    The shared ``db_session`` fixture pins ``current_org`` to DEFAULT_ORG, but the
    seed must run with NO ambient context (it self-scopes per org via ``use_org``).
    We temporarily clear the context for the seed, then restore it.
    """
    from app.tenant import current_org

    token = current_org.set(None)
    try:
        await seed(db_session)
    finally:
        current_org.reset(token)

    org_ids = [
        row[0] for row in (await db_session.execute(select(Organization.id))).all()
    ]
    # Exclude the DEFAULT_ORG test org (it has no curriculum).
    return org_ids


async def _legacy_in_version_assets(
    session: AsyncSession, version: Version
) -> list[Asset]:
    """Assets present in a legacy Version (the read-path predicate)."""
    module_ids = [
        r[0]
        for r in (
            await session.execute(
                select(Module.id).where(Module.version_id == version.id)
            )
        ).all()
    ]
    project_ids = [
        r[0]
        for r in (
            await session.execute(
                select(Project.id).where(Project.version_id == version.id)
            )
        ).all()
    ]
    if not module_ids and not project_ids:
        return []
    clauses = []
    if module_ids:
        clauses.append(Asset.module_id.in_(module_ids))
    if project_ids:
        clauses.append(Asset.project_id.in_(project_ids))
    return list(
        (await session.execute(select(Asset).where(or_(*clauses)))).scalars().all()
    )


async def _legacy_in_version_edge_count(
    session: AsyncSession, asset_ids: set[uuid.UUID]
) -> int:
    """Count DependencyEdges with BOTH endpoints in ``asset_ids`` (graph rule)."""
    if not asset_ids:
        return 0
    edges = (
        (
            await session.execute(
                select(DependencyEdge).where(
                    DependencyEdge.from_asset_id.in_(asset_ids),
                    DependencyEdge.to_asset_id.in_(asset_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(edges)


@pytest.mark.asyncio
async def test_backfill_counts_reconcile(db_session: AsyncSession):
    """Per curriculum-version: members == in-version assets, edges == in-version
    edges; one CurriculumVersion per legacy Version."""
    org_ids = await _seed_both_orgs(db_session)

    created = await backfill_content_model(db_session)
    # Sanity: aggregate creation counts are non-trivial.
    assert created["curriculum_versions"] >= 4
    assert created["version_members"] > 0
    assert created["version_edges"] > 0

    checked_versions = 0
    for org_id in org_ids:
        with use_org(org_id):
            versions = (
                (await db_session.execute(select(Version))).scalars().all()
            )
            curricula = (
                (await db_session.execute(select(Curriculum))).scalars().all()
            )
            if not versions:
                continue  # DEFAULT_ORG (no curriculum)

            # One CurriculumVersion per legacy Version (per curriculum).
            for cur in curricula:
                n_legacy = await db_session.scalar(
                    select(func.count())
                    .select_from(Version)
                    .where(Version.curriculum_id == cur.id)
                )
                n_new = await db_session.scalar(
                    select(func.count())
                    .select_from(CurriculumVersion)
                    .where(CurriculumVersion.curriculum_id == cur.id)
                )
                assert n_new == n_legacy, (
                    f"curriculum {cur.slug}: {n_new} CurriculumVersions != "
                    f"{n_legacy} legacy Versions"
                )

            for v in versions:
                checked_versions += 1
                cversion = await db_session.scalar(
                    select(CurriculumVersion).where(
                        CurriculumVersion.curriculum_id == v.curriculum_id,
                        CurriculumVersion.major == v.major,
                        CurriculumVersion.minor == v.minor,
                        CurriculumVersion.patch == v.patch,
                    )
                )
                assert cversion is not None
                # Carries semver + status.
                assert (cversion.major, cversion.minor, cversion.patch) == (
                    v.major,
                    v.minor,
                    v.patch,
                )
                assert cversion.status == v.status

                legacy_assets = await _legacy_in_version_assets(db_session, v)
                legacy_asset_ids = {a.id for a in legacy_assets}
                expected_members = len(legacy_assets)
                expected_edges = await _legacy_in_version_edge_count(
                    db_session, legacy_asset_ids
                )

                got_members = await db_session.scalar(
                    select(func.count())
                    .select_from(VersionMember)
                    .where(VersionMember.curriculum_version_id == cversion.id)
                )
                got_edges = await db_session.scalar(
                    select(func.count())
                    .select_from(VersionEdge)
                    .where(VersionEdge.curriculum_version_id == cversion.id)
                )
                assert got_members == expected_members, (
                    f"{v.curriculum_id} {v.major}.{v.minor}.{v.patch}: "
                    f"{got_members} members != {expected_members} in-version assets"
                )
                assert got_edges == expected_edges, (
                    f"{v.curriculum_id} {v.major}.{v.minor}.{v.patch}: "
                    f"{got_edges} edges != {expected_edges} in-version edges"
                )

    assert checked_versions >= 4  # 2 versions x 2 curricula


@pytest.mark.asyncio
async def test_backfill_parent_version_by_semver(db_session: AsyncSession):
    """parent_version_id is set by semver order within the curriculum.

    For the seed (0.9.0 archived, 1.0.0 active): 1.0.0's parent is 0.9.0 and
    0.9.0 has no parent.
    """
    org_ids = await _seed_both_orgs(db_session)
    await backfill_content_model(db_session)

    for org_id in org_ids:
        with use_org(org_id):
            curricula = (
                (await db_session.execute(select(Curriculum))).scalars().all()
            )
            for cur in curricula:
                cversions = (
                    (
                        await db_session.execute(
                            select(CurriculumVersion).where(
                                CurriculumVersion.curriculum_id == cur.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_semver = {
                    (c.major, c.minor, c.patch): c for c in cversions
                }
                lowest = min(by_semver)
                assert by_semver[lowest].parent_version_id is None
                # Every non-lowest version points at the immediately-lower one.
                ordered = sorted(by_semver)
                for prev, cur_sv in zip(ordered, ordered[1:]):
                    child = by_semver[cur_sv]
                    assert child.parent_version_id == by_semver[prev].id


@pytest.mark.asyncio
async def test_content_hash_integrity(db_session: AsyncSession):
    """fsck: every ContentVersion hashes to its stored content_hash."""
    org_ids = await _seed_both_orgs(db_session)
    await backfill_content_model(db_session)

    total = 0
    for org_id in org_ids:
        with use_org(org_id):
            rows = (
                await db_session.execute(
                    select(ContentVersion, LineageAsset.kind).join(
                        LineageAsset, LineageAsset.id == ContentVersion.asset_id
                    )
                )
            ).all()
            for cv, kind in rows:
                total += 1
                recomputed = content_hash(kind.value, cv.content, cv.metadata_)
                assert cv.content_hash == recomputed, (
                    f"hash mismatch for ContentVersion {cv.id} "
                    f"(asset {cv.asset_id}, seq {cv.seq})"
                )
    assert total > 0


@pytest.mark.asyncio
async def test_backfill_idempotent(db_session: AsyncSession):
    """Running the back-fill twice creates no new rows; counts are identical."""
    org_ids = await _seed_both_orgs(db_session)

    first = await backfill_content_model(db_session)
    # All five tables got rows on the first run.
    assert all(v > 0 for v in first.values()), first

    # Snapshot absolute row counts (across both orgs).
    async def _absolute_counts() -> dict[str, int]:
        out: dict[str, int] = {}
        for org_id in org_ids:
            with use_org(org_id):
                for name, model in (
                    ("curriculum_versions", CurriculumVersion),
                    ("lineage_assets", LineageAsset),
                    ("content_versions", ContentVersion),
                    ("version_members", VersionMember),
                    ("version_edges", VersionEdge),
                ):
                    n = await db_session.scalar(
                        select(func.count()).select_from(model)
                    )
                    out[name] = out.get(name, 0) + n
        return out

    after_first = await _absolute_counts()

    second = await backfill_content_model(db_session)
    after_second = await _absolute_counts()

    # Second run creates nothing.
    assert second == {k: 0 for k in second}, second
    # Absolute row counts unchanged.
    assert after_first == after_second, (after_first, after_second)


@pytest.mark.asyncio
async def test_seed_does_not_exercise_structural_sharing(db_session: AsyncSession):
    """Document that the seed only populates the ACTIVE version's structure.

    The archived 0.9.0 has no modules/projects -> no members -> no logical
    asset's content is shared across two curriculum-versions. Sharing is
    therefore not exercised by the seed (it's covered by the dedup unit test
    below instead).
    """
    org_ids = await _seed_both_orgs(db_session)
    await backfill_content_model(db_session)

    for org_id in org_ids:
        with use_org(org_id):
            curricula = (
                (await db_session.execute(select(Curriculum))).scalars().all()
            )
            for cur in curricula:
                cversions = (
                    (
                        await db_session.execute(
                            select(CurriculumVersion).where(
                                CurriculumVersion.curriculum_id == cur.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                # Exactly one cversion has members (the active one); the archived
                # one has zero. So no asset_version_id is referenced by members of
                # two different curriculum-versions.
                with_members = 0
                for cv in cversions:
                    n = await db_session.scalar(
                        select(func.count())
                        .select_from(VersionMember)
                        .where(VersionMember.curriculum_version_id == cv.id)
                    )
                    if n > 0:
                        with_members += 1
                assert with_members <= 1, (
                    f"{cur.slug}: {with_members} curriculum-versions carry "
                    "members — seed unexpectedly populates >1 version's structure"
                )

    # Cross-version sharing is therefore not exercised by the seed; the dedup
    # that ENABLES it is proven directly in test_content_version_dedup below.


@pytest.mark.asyncio
async def test_content_version_dedup(db_session: AsyncSession):
    """Two identical legacy contents for one lineage collapse to ONE row.

    This is the structural-sharing primitive: same ``asset_id`` + same
    ``content_hash`` reuses the existing ContentVersion instead of inserting a
    duplicate. Driven directly through the back-fill helper.
    """
    # A curriculum + legacy Asset with two AssetVersions whose body/metadata are
    # byte-identical (only the surrogate ids differ).
    cur = Curriculum(name="Dedup", slug=f"dedup-{uuid.uuid4().hex[:8]}")
    db_session.add(cur)
    await db_session.flush()

    asset = Asset(kind=AssetKind.lesson_plan, key="dedup/wk01/lesson_plan")
    db_session.add(asset)
    await db_session.flush()

    same_body = "# identical body\n\nSame across both revisions."
    same_meta = {"week": 1, "kind": "lesson_plan"}
    av1 = AssetVersion(
        asset_id=asset.id, major=1, minor=0, patch=0,
        body_ref=same_body, metadata_=same_meta,
    )
    av2 = AssetVersion(
        asset_id=asset.id, major=1, minor=1, patch=0,
        body_ref=same_body, metadata_=same_meta,
    )
    db_session.add_all([av1, av2])
    await db_session.flush()

    lineage = await _get_or_create_lineage_asset(db_session, asset)
    cv_map = await _backfill_content_versions(db_session, lineage, [av1, av2])

    # Both legacy AssetVersions map to the SAME ContentVersion row (dedup).
    assert cv_map[av1.id].id == cv_map[av2.id].id

    # Exactly one ContentVersion row exists for this lineage.
    n = await db_session.scalar(
        select(func.count())
        .select_from(ContentVersion)
        .where(ContentVersion.asset_id == lineage.id)
    )
    assert n == 1

    # And a genuinely-different content adds a second, distinct row.
    av3 = AssetVersion(
        asset_id=asset.id, major=1, minor=2, patch=0,
        body_ref="# different body", metadata_=same_meta,
    )
    db_session.add(av3)
    await db_session.flush()
    cv_map2 = await _backfill_content_versions(db_session, lineage, [av1, av2, av3])
    assert cv_map2[av3.id].id != cv_map2[av1.id].id
    n2 = await db_session.scalar(
        select(func.count())
        .select_from(ContentVersion)
        .where(ContentVersion.asset_id == lineage.id)
    )
    assert n2 == 2
