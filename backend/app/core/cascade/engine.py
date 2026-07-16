"""Dependency-cascade engine for CurricMesh — Task B1.

Edge-direction convention (used throughout this module):
    DependencyEdge(from_asset_id=A, to_asset_id=B)  means B depends on A.
    A is the upstream/foundational asset; B is the downstream dependent.
    Therefore, a change to A cascades **to** B (follow outgoing edges of A).

Architecture:
    Pure traversal core  — cascade(), alignment_report()
    DB-backed wrappers   — cascade_for_asset(), alignment_report_for_version()

The pure functions accept any iterable of objects/namedtuples with
`from_asset_id` and `to_asset_id` attributes, making them trivially
unit-testable without a database.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Iterable

from app.core.versioning.semver import BumpType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class ProposedBump:
    """A recommended version bump for a single asset produced by cascade().

    Attributes:
        asset_id:  The asset that should be bumped.
        reason:    Human-readable explanation (e.g. which upstream changed).
        bump_type: How much to bump; defaults to minor per framework rule 2.1
                   (downstream dependents get a minor bump on upstream change).
    """

    asset_id: uuid.UUID
    reason: str
    bump_type: BumpType = field(default=BumpType.minor)


@dataclass
class Misalignment:
    """A detected staleness: a dependent asset hasn't been updated since its dependency changed.

    Attributes:
        dependent_asset_id:  The asset that is stale (B in the A→B edge).
        dependency_asset_id: The upstream asset that changed more recently (A).
        reason:              Human-readable description of the staleness.
    """

    dependent_asset_id: uuid.UUID
    dependency_asset_id: uuid.UUID
    reason: str


# ---------------------------------------------------------------------------
# Pure traversal — cascade
# ---------------------------------------------------------------------------


def cascade(
    start_asset_id: uuid.UUID,
    edges: Iterable,
) -> list[ProposedBump]:
    """BFS from start_asset_id following outgoing edges to find all dependents.

    For each reachable dependent (i.e. every asset B reachable via A→B edges
    starting from start_asset_id), return a ProposedBump(asset_id=B, ...).

    The start asset itself is never included in the result.

    Cycle-safe: a visited set prevents infinite loops even if the dependency
    graph has cycles (which can happen accidentally in real curricula).

    Args:
        start_asset_id: The asset whose change triggered the cascade.
        edges:          Iterable of objects with `from_asset_id` and
                        `to_asset_id` attributes (e.g. DependencyEdge rows,
                        SimpleNamespace stubs, or namedtuples).

    Returns:
        List of ProposedBump — one per reachable dependent, in BFS order.
        Each bump defaults to BumpType.minor (framework rule 2.1).
    """
    # Build adjacency map: asset_id → set of direct dependent IDs
    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        dependents = adjacency.setdefault(edge.from_asset_id, set())
        dependents.add(edge.to_asset_id)

    # BFS — visited set prevents revisiting (and infinite loops in cycles)
    visited: set[uuid.UUID] = {start_asset_id}
    queue: deque[uuid.UUID] = deque(adjacency.get(start_asset_id, set()))

    # Seed queue with direct dependents; mark them visited immediately to
    # avoid duplicates even in diamond/cycle topologies
    for dep in list(queue):
        visited.add(dep)

    bumps: list[ProposedBump] = []

    while queue:
        current = queue.popleft()
        bumps.append(
            ProposedBump(
                asset_id=current,
                reason=f"upstream asset {start_asset_id} changed",
            )
        )
        # Enqueue next-level dependents not yet visited
        for next_dep in adjacency.get(current, set()):
            if next_dep not in visited:
                visited.add(next_dep)
                queue.append(next_dep)

    return bumps


# ---------------------------------------------------------------------------
# Pure analysis — alignment_report
# ---------------------------------------------------------------------------


def alignment_report(
    edges: Iterable,
    latest_change_at: dict[uuid.UUID, datetime],
) -> list[Misalignment]:
    """Detect stale dependents by comparing update timestamps across edges.

    For each edge A→B (B depends on A):
        If B's latest change predates A's latest change → B is stale.
        Emit a Misalignment(dependent_asset_id=B, dependency_asset_id=A).

    Edges where either asset lacks an entry in latest_change_at are skipped
    gracefully (not every asset in the graph may have a version yet).

    Args:
        edges:            Iterable of objects with `from_asset_id` / `to_asset_id`.
        latest_change_at: Maps each asset_id → created_at of its most recent
                          AssetVersion.  Missing keys are silently skipped.

    Returns:
        List of Misalignment objects (one per stale dependent edge).
    """
    misalignments: list[Misalignment] = []

    for edge in edges:
        dep_id = edge.from_asset_id    # upstream / dependency
        dependent_id = edge.to_asset_id  # downstream / dependent

        dep_ts = latest_change_at.get(dep_id)
        dependent_ts = latest_change_at.get(dependent_id)

        # Skip edges where we don't have timestamps for both sides
        if dep_ts is None or dependent_ts is None:
            continue

        # Stale: dependent was last updated BEFORE its dependency
        if dependent_ts < dep_ts:
            misalignments.append(
                Misalignment(
                    dependent_asset_id=dependent_id,
                    dependency_asset_id=dep_id,
                    reason=(
                        f"Dependent asset {dependent_id} was last updated "
                        f"{dependent_ts.isoformat()} which predates its dependency "
                        f"{dep_id} last updated {dep_ts.isoformat()}."
                    ),
                )
            )

    return misalignments


# ---------------------------------------------------------------------------
# DB-backed wrappers (thin async layer)
# ---------------------------------------------------------------------------


async def cascade_for_asset(
    session: "AsyncSession",
    asset_id: uuid.UUID,
) -> list[ProposedBump]:
    """Load all DependencyEdges from the DB and run the pure cascade.

    Args:
        session:  Active AsyncSession.
        asset_id: The asset whose change triggered the cascade.

    Returns:
        List of ProposedBump for every transitively reachable dependent.
    """
    from sqlalchemy import select

    from app.models.graph import DependencyEdge

    # MVP simplification: load the full edge table in one query.
    # Fine at demo scale; switch to a recursive CTE or version-scoped filter
    # when edge counts grow large enough to matter.
    result = await session.execute(select(DependencyEdge))
    all_edges = result.scalars().all()
    return cascade(asset_id, all_edges)


async def alignment_report_for_version(
    session: "AsyncSession",
    version_id: uuid.UUID,
) -> list[Misalignment]:
    """Load edges + latest AssetVersion timestamps for a curriculum version, run alignment_report.

    Loads:
      - All Modules and Projects belonging to this version.
      - All Assets belonging to those modules/projects.
      - All DependencyEdges between those assets.
      - Each asset's most recent AssetVersion (by created_at) to build
        the latest_change_at mapping.

    Args:
        session:    Active AsyncSession.
        version_id: The curriculum Version to inspect.

    Returns:
        List of Misalignment objects for stale dependent assets.
    """
    from sqlalchemy import select
    from sqlalchemy.sql import func

    from app.models.graph import DependencyEdge
    from app.models.structure import Asset, AssetVersion, Module, Project

    # Collect all asset IDs belonging to this curriculum version
    module_result = await session.execute(
        select(Module.id).where(Module.version_id == version_id)
    )
    module_ids = [row[0] for row in module_result.all()]

    project_result = await session.execute(
        select(Project.id).where(Project.version_id == version_id)
    )
    project_ids = [row[0] for row in project_result.all()]

    if not module_ids and not project_ids:
        return []

    # Load assets belonging to those modules or projects
    asset_result = await session.execute(
        select(Asset).where(
            (Asset.module_id.in_(module_ids)) | (Asset.project_id.in_(project_ids))
        )
    )
    assets = asset_result.scalars().all()
    asset_ids = [a.id for a in assets]

    if not asset_ids:
        return []

    # Load dependency edges whose upstream (from_asset_id) is within this version's
    # asset set — i.e. within-version dependencies only.  Cross-version dependencies
    # (where the upstream asset belongs to a different version) are intentionally
    # out of scope for a version-level alignment report.
    edge_result = await session.execute(
        select(DependencyEdge).where(
            DependencyEdge.from_asset_id.in_(asset_ids)
        )
    )
    edges = edge_result.scalars().all()

    # Build latest_change_at: for each asset, find max(created_at) across its AssetVersions
    latest_result = await session.execute(
        select(AssetVersion.asset_id, func.max(AssetVersion.created_at))
        .where(AssetVersion.asset_id.in_(asset_ids))
        .group_by(AssetVersion.asset_id)
    )
    latest_change_at: dict[uuid.UUID, datetime] = {
        row[0]: row[1] for row in latest_result.all()
    }

    return alignment_report(edges, latest_change_at)
