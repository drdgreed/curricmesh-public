"""Router: GET /api/v1/curricula/{curriculum_id}/graph

Returns the dependency graph for a curriculum's active version:
  - nodes: all Assets with their latest AssetVersion metadata
  - edges: DependencyEdges between those assets
  - misaligned_asset_ids: stale dependents per alignment_report_for_version

Read-path strategy (M2 strangler):
  When the curriculum has a populated immutable manifest
  (``active_curriculum_version`` resolves), the graph is built **from the
  manifest** (``version_members`` / ``version_edges`` / ``manifest_alignment``).
  Otherwise it falls back to the legacy structure-table query below, so curricula
  that have not been back-filled (and the existing old-model tests) keep working
  unchanged.

  The manifest is the source of truth for *which* nodes/edges exist and *which*
  are misaligned (that is what the port proves equivalent to the legacy path).
  The per-node **display** fields — friendly label, latest semver, lifecycle
  status — have no home in the immutable content model yet (it carries ``seq``,
  not semver, and no status), so they are resolved from the legacy structure
  tables keyed on the stable lineage key (``LineageAsset.lineage_key`` ==
  ``Asset.key``). ``app/core/manifest.py`` itself stays pure (new model only);
  only this strangler router bridges the two models during migration.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.auth.rbac import get_current_user
from app.core.cascade.engine import alignment_report_for_version
from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment,
    version_edges,
    version_members,
)
from app.core.naming import asset_display_names
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.graph import DependencyEdge
from app.models.structure import Asset, AssetVersion, Module, Project
from app.schemas.graph import GraphEdge, GraphNode, GraphOut

router = APIRouter(prefix="/api/v1/curricula", tags=["graph"])


@router.get("/{curriculum_id}/graph", response_model=GraphOut)
async def get_curriculum_graph(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GraphOut:
    """Return the dependency graph for the curriculum's active version.

    Builds from the immutable manifest when one is populated; otherwise falls
    back to the legacy structure-table query.
    """
    # 404 if curriculum not found
    result = await db.execute(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    curriculum = result.scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    # --- Strangler switch: manifest path when a back-filled version exists. ---
    cversion = await active_curriculum_version(db, curriculum_id)
    if cversion is not None:
        return await _graph_from_manifest(db, cversion.id)

    return await _graph_from_legacy(db, curriculum)


# ---------------------------------------------------------------------------
# Manifest read path (M2)
# ---------------------------------------------------------------------------


async def _graph_from_manifest(
    db: AsyncSession, curriculum_version_id: uuid.UUID
) -> GraphOut:
    """Build ``GraphOut`` from the immutable manifest read layer.

    Nodes come from ``version_members`` (node id = the member's ``LineageAsset``
    id), edges from ``version_edges`` (logical from/to), and the misaligned set
    from ``manifest_alignment`` (``LineageAsset`` ids). The friendly label, latest
    semver and lifecycle status are resolved from the legacy structure tables via
    the stable lineage key (== ``Asset.key``), reproducing the legacy node shape.
    """
    members = await version_members(db, curriculum_version_id)
    edges = await version_edges(db, curriculum_version_id)
    misaligned_ids = await manifest_alignment(db, curriculum_version_id)

    if not members:
        return GraphOut(nodes=[], edges=[], misaligned_asset_ids=[])

    # Resolve the legacy Assets backing these lineage assets, keyed on the shared
    # stable key (LineageAsset.lineage_key == Asset.key). One batched query.
    lineage_keys = [m.lineage_key for m in members]
    legacy_assets = (
        (
            await db.execute(
                select(Asset).where(Asset.key.in_(lineage_keys))
            )
        )
        .scalars()
        .all()
    )
    asset_by_key: dict[str, Asset] = {a.key: a for a in legacy_assets}
    legacy_asset_ids = [a.id for a in legacy_assets]

    # Friendly labels (legacy resolver) keyed back to the lineage key.
    display_by_asset_id = await asset_display_names(db, legacy_asset_ids)
    label_by_key: dict[str, str] = {
        a.key: display_by_asset_id.get(a.id, a.key) for a in legacy_assets
    }

    # Latest semver + status per legacy asset (the row with max(created_at) —
    # the same selection the legacy graph + the back-fill's member selection use).
    semver_status_by_key = await _latest_semver_status_by_key(
        db, asset_by_key
    )

    # Node/edge ids must be the LEGACY Asset ids — that's the graph API contract
    # (the frontend navigates by them) and what the golden fixtures key on. Map
    # each LineageAsset id -> its legacy Asset id via the shared lineage key.
    legacy_id_by_lineage_id: dict = {
        m.asset_id: asset_by_key[m.lineage_key].id
        for m in members
        if m.lineage_key in asset_by_key
    }

    nodes: list[GraphNode] = [
        GraphNode(
            id=legacy_id_by_lineage_id.get(m.asset_id, m.asset_id),
            kind=m.kind,
            label=label_by_key.get(m.lineage_key, m.lineage_key),
            latest_version=semver_status_by_key.get(m.lineage_key, (None, None))[0],
            status=semver_status_by_key.get(m.lineage_key, (None, None))[1],
        )
        for m in members
    ]

    graph_edges: list[GraphEdge] = [
        GraphEdge(
            from_asset_id=legacy_id_by_lineage_id.get(e.from_asset_id, e.from_asset_id),
            to_asset_id=legacy_id_by_lineage_id.get(e.to_asset_id, e.to_asset_id),
            edge_type=e.edge_type,
        )
        for e in edges
    ]

    return GraphOut(
        nodes=nodes,
        edges=graph_edges,
        misaligned_asset_ids=[
            legacy_id_by_lineage_id.get(i, i) for i in misaligned_ids
        ],
    )


async def _latest_semver_status_by_key(
    db: AsyncSession, asset_by_key: dict[str, "Asset"]
) -> dict[str, tuple[str | None, Any]]:
    """Map each lineage key to ``(latest_semver, latest_status)`` from legacy.

    The "latest" AssetVersion is the one with the maximum ``created_at`` per asset
    — the same row the legacy graph endpoint surfaces and the back-fill selects as
    each member's content. One batched query (max-created_at self-join), no N+1.
    """
    asset_ids = [a.id for a in asset_by_key.values()]
    if not asset_ids:
        return {}

    latest_subq = (
        select(
            AssetVersion.asset_id,
            func.max(AssetVersion.created_at).label("max_ts"),
        )
        .where(AssetVersion.asset_id.in_(asset_ids))
        .group_by(AssetVersion.asset_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(AssetVersion).join(
                latest_subq,
                (AssetVersion.asset_id == latest_subq.c.asset_id)
                & (AssetVersion.created_at == latest_subq.c.max_ts),
            )
        )
    ).scalars().all()
    latest_by_asset_id = {av.asset_id: av for av in rows}

    out: dict[str, tuple[str | None, Any]] = {}
    for key, asset in asset_by_key.items():
        av = latest_by_asset_id.get(asset.id)
        if av is None:
            out[key] = (None, None)
        else:
            out[key] = (f"{av.major}.{av.minor}.{av.patch}", av.status)
    return out


# ---------------------------------------------------------------------------
# Legacy read path (fallback — unchanged behavior)
# ---------------------------------------------------------------------------


async def _graph_from_legacy(
    db: AsyncSession, curriculum: Curriculum
) -> GraphOut:
    """The pre-M2 structure-table graph build (fallback when no manifest)."""
    active_version_id = curriculum.current_version_id
    if active_version_id is None:
        return GraphOut(nodes=[], edges=[], misaligned_asset_ids=[])

    # Collect module IDs and project IDs for this version
    mod_result = await db.execute(
        select(Module.id).where(Module.version_id == active_version_id)
    )
    module_ids = [row[0] for row in mod_result.all()]

    proj_result = await db.execute(
        select(Project.id).where(Project.version_id == active_version_id)
    )
    project_ids = [row[0] for row in proj_result.all()]

    if not module_ids and not project_ids:
        return GraphOut(nodes=[], edges=[], misaligned_asset_ids=[])

    # Load all assets in this version
    asset_result = await db.execute(
        select(Asset).where(
            (Asset.module_id.in_(module_ids)) | (Asset.project_id.in_(project_ids))
        )
    )
    assets = asset_result.scalars().all()
    asset_ids = [a.id for a in assets]

    if not asset_ids:
        return GraphOut(nodes=[], edges=[], misaligned_asset_ids=[])

    # Load the latest AssetVersion (by created_at) per asset — one query
    # Assumption: AssetVersions are append-only and semver-monotone, so the
    # row with the maximum created_at timestamp is always the highest semver.
    # This mirrors the same assumption in alignment_report_for_version.
    # Subquery: max created_at per asset_id
    latest_subq = (
        select(
            AssetVersion.asset_id,
            func.max(AssetVersion.created_at).label("max_ts"),
        )
        .where(AssetVersion.asset_id.in_(asset_ids))
        .group_by(AssetVersion.asset_id)
        .subquery()
    )
    av_result = await db.execute(
        select(AssetVersion).join(
            latest_subq,
            (AssetVersion.asset_id == latest_subq.c.asset_id)
            & (AssetVersion.created_at == latest_subq.c.max_ts),
        )
    )
    latest_avs: dict[uuid.UUID, AssetVersion] = {
        av.asset_id: av for av in av_result.scalars().all()
    }

    # Load edges where BOTH endpoints are within this version's assets.
    # This guarantees the response never references a node not present in nodes[].
    edge_result = await db.execute(
        select(DependencyEdge).where(
            DependencyEdge.from_asset_id.in_(asset_ids),
            DependencyEdge.to_asset_id.in_(asset_ids),
        )
    )
    edges_orm = edge_result.scalars().all()

    # Compute misaligned asset IDs via the reusable engine function
    misalignments = await alignment_report_for_version(db, active_version_id)
    misaligned_ids = list({m.dependent_asset_id for m in misalignments})

    # Resolve friendly node labels in one batched lookup (no N+1).
    display_names = await asset_display_names(db, asset_ids)

    # Build graph nodes
    nodes: list[GraphNode] = []
    for asset in assets:
        av = latest_avs.get(asset.id)
        latest_version = (
            f"{av.major}.{av.minor}.{av.patch}" if av is not None else None
        )
        status = av.status if av is not None else None
        nodes.append(
            GraphNode(
                id=asset.id,
                kind=asset.kind,
                label=display_names.get(asset.id, asset.key),
                latest_version=latest_version,
                status=status,
            )
        )

    # Build graph edges
    graph_edges: list[GraphEdge] = [
        GraphEdge(
            from_asset_id=e.from_asset_id,
            to_asset_id=e.to_asset_id,
            edge_type=e.edge_type,
        )
        for e in edges_orm
    ]

    return GraphOut(nodes=nodes, edges=graph_edges, misaligned_asset_ids=misaligned_ids)
