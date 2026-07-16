"""Back-fill the immutable content model from the legacy structure tables.

Strangler step 2 (``docs/specs/2026-06-06-immutable-version-model-design.md`` §5):
populate the five new tables (``lineage_assets``, ``content_versions``,
``curriculum_versions``, ``version_members``, ``version_edges``) from today's
``curricula``/``versions``/``modules``/``projects``/``assets``/``asset_versions``/
``dependency_edges`` — **without touching any read path**. Nothing reads the new
model yet; this just lands the data so Milestone-2 can port reads against it.

Mapping (verified against the seeded DB, not assumed)
-----------------------------------------------------
* **CurriculumVersion** ← every legacy ``Version`` of every ``Curriculum``.
  Carries semver + status; ``parent_version_id`` is the immediately-lower
  semver version of the same curriculum (so 1.0.0's parent is 0.9.0).

* **LineageAsset** ← every distinct logical asset, deduped by ``lineage_key`` =
  the legacy ``Asset.key`` (which is version-independent — the SAME key across
  versions is the SAME lineage). The seed gives every ``Asset`` a unique key, so
  this is 1:1 here, but the dedup is what makes lineage work once forks reuse a
  key across curriculum-versions.

* **ContentVersion** ← every legacy ``AssetVersion``. ``seq`` is assigned 1..N by
  the asset-version's semver order within its lineage. ``content`` comes from
  ``body_ref``; ``metadata_`` is carried across; ``content_hash`` is recomputed
  via :func:`app.core.content_hash.content_hash`. ``created_at`` carries the
  legacy ``AssetVersion.created_at`` so the manifest's timestamp-based staleness
  reproduces today's alignment exactly (§5 golden equivalence). **Identical
  content for the same lineage (same ``asset_id`` + same ``content_hash``) is
  deduped to one row** — this is what enables structural sharing.

* **VersionMember** ← one row per (curriculum-version, asset present in that
  version). "Present" is computed exactly as the read paths compute it: an asset
  is in a version when its owning ``Module``/``Project`` belongs to that version
  (``Module.version_id``/``Project.version_id``). The selected content revision
  is the asset's **latest** ``ContentVersion`` (the one the graph/alignment read
  path surfaces via ``max(created_at)``); ``section``/``week_index`` come from the
  owning ``Module.focus``/``Module.index`` (project assets → a "Projects" section,
  ``week_index = 0``). ``order`` is a stable ordering (week_index, key).

* **VersionEdge** ← one row per (curriculum-version, in-version
  ``DependencyEdge``). "In-version" mirrors the graph endpoint: BOTH endpoints
  must be assets present in that curriculum-version. Endpoints are the *logical*
  ``LineageAsset`` ids (never remapped). ``validated_against_seq = None`` so the
  migrated model reproduces today's timestamp-based staleness exactly (§5 note —
  golden equivalence).

Tenant scoping
--------------
Every new row is ``TenantScoped`` (write-stamped ``organization_id`` from the
ambient ``current_org``), and the legacy reads are app-layer-filtered to the same
org. So the back-fill runs **per organization** inside ``use_org`` — exactly like
``seed.bootcamp_curriculum``. :func:`backfill_content_model` discovers every org
itself and processes each in its own context, so a single call back-fills the
whole DB.

Idempotency
-----------
Re-runnable. Every insert is guarded by a natural key (look-up-or-create):

* ``CurriculumVersion``  → ``(curriculum_id, major, minor, patch)``
* ``LineageAsset``       → ``(organization_id, lineage_key)``  [org via context]
* ``ContentVersion``     → ``(asset_id, content_hash)``        [dedup / sharing]
* ``VersionMember``      → ``(curriculum_version_id, asset_id)``
* ``VersionEdge``        → ``(curriculum_version_id, from_asset_id, to_asset_id,
  edge_type)``

A second run finds every natural key already present and creates nothing new, so
counts are stable.
"""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.graph import DependencyEdge
from app.models.org import Organization
from app.models.structure import Asset, AssetVersion, Module, Project
from app.models.version import Version
from app.database import bind_session_org
from app.tenant import use_org

# Placement for assets owned by a Project rather than a Module. Projects have no
# week index in the legacy model, so they land in a synthetic "Projects" section.
_PROJECT_SECTION = "Projects"
_PROJECT_WEEK_INDEX = 0


def _semver_key(obj) -> tuple[int, int, int]:
    return (obj.major, obj.minor, obj.patch)


async def _get_or_create_lineage_asset(
    session: AsyncSession, asset: Asset
) -> LineageAsset:
    """Look up (by ``lineage_key``) or create the LineageAsset for a legacy Asset.

    ``lineage_key`` = the legacy ``Asset.key`` (version-independent). Deduped
    within the active org (the SELECT is auto-filtered to ``current_org``), so the
    same key across versions collapses to one lineage row.
    """
    existing = await session.scalar(
        select(LineageAsset).where(LineageAsset.lineage_key == asset.key)
    )
    if existing is not None:
        return existing
    lineage = LineageAsset(
        kind=asset.kind,
        lineage_key=asset.key,
        source_url=None,
    )
    session.add(lineage)
    await session.flush()
    return lineage


async def _backfill_content_versions(
    session: AsyncSession, lineage: LineageAsset, legacy_avs: list[AssetVersion]
) -> dict[uuid.UUID, ContentVersion]:
    """Create ContentVersions for a lineage's legacy AssetVersions (deduped).

    Returns a map ``legacy_asset_version_id -> ContentVersion`` so members can
    point at the right immutable row. ``seq`` is assigned 1..N by semver order.
    Identical content (same ``content_hash``) for this lineage reuses one row.
    """
    ordered = sorted(legacy_avs, key=_semver_key)
    by_hash: dict[str, ContentVersion] = {}
    result: dict[uuid.UUID, ContentVersion] = {}

    for av in ordered:
        ch = content_hash(lineage.kind.value, av.body_ref, av.metadata_)

        # Dedup within this lineage: identical content -> one ContentVersion.
        # Check already-created-this-run first, then the DB (idempotency).
        cv = by_hash.get(ch)
        if cv is None:
            cv = await session.scalar(
                select(ContentVersion).where(
                    ContentVersion.asset_id == lineage.id,
                    ContentVersion.content_hash == ch,
                )
            )
        if cv is None:
            # Determine the next free seq from the DB so re-runs don't collide.
            max_seq = await session.scalar(
                select(ContentVersion.seq)
                .where(ContentVersion.asset_id == lineage.id)
                .order_by(ContentVersion.seq.desc())
                .limit(1)
            )
            seq = (max_seq or 0) + 1
            cv = ContentVersion(
                asset_id=lineage.id,
                seq=seq,
                content=av.body_ref or "",
                metadata_=av.metadata_,
                content_hash=ch,
                # Carry the legacy AssetVersion's historical timestamp so the
                # manifest's timestamp-based staleness (alignment) reproduces
                # today's output exactly (§5 — golden equivalence). Without this
                # every back-filled row would default to now() and the staleness
                # relation the seed manufactures would be lost.
                created_at=av.created_at,
                created_by=None,
            )
            session.add(cv)
            await session.flush()
        by_hash[ch] = cv
        result[av.id] = cv

    return result


async def _backfill_one_org(session: AsyncSession, counts: Counter) -> None:
    """Back-fill every curriculum in the *current* tenant context."""
    curricula = (await session.execute(select(Curriculum))).scalars().all()

    for curriculum in curricula:
        # --- Versions of this curriculum, in semver order (for parent links) ---
        versions = (
            (
                await session.execute(
                    select(Version).where(Version.curriculum_id == curriculum.id)
                )
            )
            .scalars()
            .all()
        )
        versions_sorted = sorted(versions, key=_semver_key)

        # 1) CurriculumVersion per legacy Version; parent = next-lower semver.
        cv_by_version_id: dict[uuid.UUID, CurriculumVersion] = {}
        prev_cversion: CurriculumVersion | None = None
        for v in versions_sorted:
            cversion = await session.scalar(
                select(CurriculumVersion).where(
                    CurriculumVersion.curriculum_id == curriculum.id,
                    CurriculumVersion.major == v.major,
                    CurriculumVersion.minor == v.minor,
                    CurriculumVersion.patch == v.patch,
                )
            )
            if cversion is None:
                cversion = CurriculumVersion(
                    curriculum_id=curriculum.id,
                    major=v.major,
                    minor=v.minor,
                    patch=v.patch,
                    status=v.status,
                    parent_version_id=(prev_cversion.id if prev_cversion else None),
                )
                session.add(cversion)
                await session.flush()
                counts["curriculum_versions"] += 1
            cv_by_version_id[v.id] = cversion
            prev_cversion = cversion

        # --- Build the structure for each version: which assets are in it. ---
        for v in versions_sorted:
            cversion = cv_by_version_id[v.id]

            # Modules + Projects belonging to this version (the structure owner).
            modules = (
                (
                    await session.execute(
                        select(Module).where(Module.version_id == v.id)
                    )
                )
                .scalars()
                .all()
            )
            projects = (
                (
                    await session.execute(
                        select(Project).where(Project.version_id == v.id)
                    )
                )
                .scalars()
                .all()
            )
            module_by_id = {m.id: m for m in modules}
            project_by_id = {p.id: p for p in projects}
            module_ids = list(module_by_id)
            project_ids = list(project_by_id)

            if not module_ids and not project_ids:
                # No structure under this version (e.g. archived 0.9.0 in the
                # seed) -> zero members, zero edges. The CurriculumVersion row
                # still exists (a real, empty manifest).
                continue

            # Assets present in this version (exactly the read-path predicate:
            # owning Module/Project belongs to this version). Build the OR of the
            # membership clauses that actually apply.
            from sqlalchemy import or_

            membership_clauses = []
            if module_ids:
                membership_clauses.append(Asset.module_id.in_(module_ids))
            if project_ids:
                membership_clauses.append(Asset.project_id.in_(project_ids))
            assets = (
                (
                    await session.execute(
                        select(Asset).where(or_(*membership_clauses))
                    )
                )
                .scalars()
                .all()
            )

            # For each asset: lineage + its content versions + the SELECTED one.
            lineage_by_asset: dict[uuid.UUID, LineageAsset] = {}
            selected_cv_by_asset: dict[uuid.UUID, ContentVersion] = {}
            for asset in assets:
                lineage = await _get_or_create_lineage_asset(session, asset)
                lineage_by_asset[asset.id] = lineage

                legacy_avs = (
                    (
                        await session.execute(
                            select(AssetVersion).where(
                                AssetVersion.asset_id == asset.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                cv_map = await _backfill_content_versions(
                    session, lineage, legacy_avs
                )

                # SELECT the content revision "in" this version: the latest legacy
                # AssetVersion by created_at — exactly what the graph/alignment
                # read path surfaces (max(created_at)). Falls back to semver-max
                # if timestamps tie. Skip assets with no content (defensive).
                if legacy_avs:
                    latest_av = max(
                        legacy_avs, key=lambda a: (a.created_at, _semver_key(a))
                    )
                    selected_cv_by_asset[asset.id] = cv_map[latest_av.id]

            # 2) VersionMember per (cversion, asset present). order: stable by
            #    (week_index, lineage_key).
            placements: list[tuple[Asset, str, int]] = []
            for asset in assets:
                if asset.id not in selected_cv_by_asset:
                    continue  # asset with no AssetVersion -> nothing to place
                if asset.module_id and asset.module_id in module_by_id:
                    mod = module_by_id[asset.module_id]
                    section = mod.focus or f"Week {mod.index}"
                    week_index = mod.index
                elif asset.project_id and asset.project_id in project_by_id:
                    section = _PROJECT_SECTION
                    week_index = _PROJECT_WEEK_INDEX
                else:
                    # Owner not in this version (shouldn't happen given the query).
                    continue
                placements.append((asset, section, week_index))

            placements.sort(
                key=lambda t: (t[2], lineage_by_asset[t[0].id].lineage_key)
            )
            for order, (asset, section, week_index) in enumerate(placements):
                lineage = lineage_by_asset[asset.id]
                selected = selected_cv_by_asset[asset.id]
                existing_member = await session.scalar(
                    select(VersionMember).where(
                        VersionMember.curriculum_version_id == cversion.id,
                        VersionMember.asset_id == lineage.id,
                    )
                )
                if existing_member is None:
                    session.add(
                        VersionMember(
                            curriculum_version_id=cversion.id,
                            asset_id=lineage.id,
                            asset_version_id=selected.id,
                            section=section,
                            week_index=week_index,
                            order=order,
                        )
                    )
                    counts["version_members"] += 1

            # 3) VersionEdge per in-version DependencyEdge (BOTH endpoints in
            #    this version's asset set — mirrors the graph endpoint).
            in_version_asset_ids = {
                a.id for a in assets if a.id in lineage_by_asset
            }
            if in_version_asset_ids:
                edges = (
                    (
                        await session.execute(
                            select(DependencyEdge).where(
                                DependencyEdge.from_asset_id.in_(
                                    in_version_asset_ids
                                ),
                                DependencyEdge.to_asset_id.in_(
                                    in_version_asset_ids
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for e in edges:
                    from_lineage = lineage_by_asset[e.from_asset_id]
                    to_lineage = lineage_by_asset[e.to_asset_id]
                    existing_edge = await session.scalar(
                        select(VersionEdge).where(
                            VersionEdge.curriculum_version_id == cversion.id,
                            VersionEdge.from_asset_id == from_lineage.id,
                            VersionEdge.to_asset_id == to_lineage.id,
                            VersionEdge.edge_type == e.edge_type,
                        )
                    )
                    if existing_edge is None:
                        session.add(
                            VersionEdge(
                                curriculum_version_id=cversion.id,
                                from_asset_id=from_lineage.id,
                                to_asset_id=to_lineage.id,
                                edge_type=e.edge_type,
                                validated_against_seq=None,
                            )
                        )
                        counts["version_edges"] += 1

        await session.flush()


async def backfill_content_model(session: AsyncSession) -> dict:
    """Populate the immutable content model from the legacy tables (whole DB).

    Discovers every organization and back-fills each inside its own tenant
    context (so ``organization_id`` write-stamping + the app-layer read filter
    line up). Idempotent: re-running creates no new rows.

    Returns a dict of *newly-created* row counts per table::

        {"curriculum_versions": int, "lineage_assets": int,
         "content_versions": int, "version_members": int, "version_edges": int}

    On a second run every count is 0 (everything already exists).
    """
    counts: Counter = Counter()

    # Organizations is NOT tenant-scoped (structural identity), so list unscoped.
    org_ids = [
        row[0]
        for row in (await session.execute(select(Organization.id))).all()
    ]

    for org_id in org_ids:
        with use_org(org_id):
            # Bind the DB GUC to this org too (not just the ContextVar): under
            # production FORCE ROW LEVEL SECURITY on a non-superuser role, the
            # per-org reads/writes below are RLS-filtered on ``app.current_org``.
            # Session-scoped so it holds across the flushes in _backfill_one_org
            # and is re-pointed for each org within this single transaction (the
            # final commit is after the loop). No-op under an RLS-bypassing role
            # (dev/CI superuser), so existing tests are unaffected.
            await bind_session_org(session, org_id)
            # LineageAsset / ContentVersion rows are created via look-up-or-create
            # with dedup, so count them by before/after delta (per org, under the
            # org's read filter) rather than incrementing at each create site.
            n_lineage_before = await _count_lineage(session)
            n_cv_before = await _count_content_versions(session)
            await _backfill_one_org(session, counts)
            counts["lineage_assets"] += await _count_lineage(session) - n_lineage_before
            counts["content_versions"] += (
                await _count_content_versions(session) - n_cv_before
            )

    await session.commit()

    return {
        "curriculum_versions": counts.get("curriculum_versions", 0),
        "lineage_assets": counts.get("lineage_assets", 0),
        "content_versions": counts.get("content_versions", 0),
        "version_members": counts.get("version_members", 0),
        "version_edges": counts.get("version_edges", 0),
    }


async def _count_lineage(session: AsyncSession) -> int:
    from sqlalchemy import func

    return (
        await session.scalar(select(func.count()).select_from(LineageAsset))
    ) or 0


async def _count_content_versions(session: AsyncSession) -> int:
    from sqlalchemy import func

    return (
        await session.scalar(select(func.count()).select_from(ContentVersion))
    ) or 0
