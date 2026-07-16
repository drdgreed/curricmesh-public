"""Read/write layer for the course-content browser (Feature A).

Reads the immutable manifest (via ``app.core.manifest`` — never modifies it) and
bridges it to the **legacy ``Asset.id`` contract** the frontend navigates by,
exactly like ``app/routers/graph.py``'s ``_graph_from_manifest``:

* members come from the manifest (``LineageAsset`` ids), but every id this module
  exposes is the corresponding **legacy ``Asset.id``**, mapped via the shared
  stable key (``LineageAsset.lineage_key == Asset.key``);
* friendly labels come from ``app.core.naming.asset_display_names``;
* latest semver + lifecycle status come from the legacy ``AssetVersion`` row with
  the max ``created_at`` (the same selection the graph endpoint + the back-fill
  use).

Three entry points:

* :func:`course_calendar` — the calendar/course view (sections of tiles).
* :func:`asset_detail`    — one asset's selected content + history + relations.
* :func:`set_source_url`  — set/clear an asset's editable source link.

All queries run org-scoped through the ambient ``current_org`` like every other
read path; the caller owns tenant context.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.sql import func

from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment,
    version_edges,
    version_members,
)
from app.core.naming import asset_display_names
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.enums import AssetKind, LifecycleStatus
from app.models.structure import Asset, AssetVersion

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Value objects (lightweight read DTOs — the routers map these to schemas)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarTile:
    """A clickable asset tile (id == legacy Asset.id)."""

    id: uuid.UUID
    lineage_key: str
    kind: AssetKind
    label: str
    source_url: str | None
    latest_version: str | None
    status: LifecycleStatus | None
    misaligned: bool


@dataclass(frozen=True)
class CalendarSection:
    """Tiles grouped by (week_index, section)."""

    week_index: int
    section: str
    tiles: list[CalendarTile]


@dataclass(frozen=True)
class AssetVersionRef:
    """One entry in an asset's append-only content-version history."""

    seq: int
    content_hash: str
    created_at: datetime


@dataclass(frozen=True)
class AssetEdgeRef:
    """A related asset (prerequisite / dependent), id == legacy Asset.id."""

    id: uuid.UUID
    lineage_key: str
    label: str
    edge_type: str


@dataclass(frozen=True)
class AssetDetail:
    """An asset's selected content + history + logical relations."""

    id: uuid.UUID
    lineage_key: str
    kind: AssetKind
    label: str
    source_url: str | None
    content: str
    content_metadata: dict | None
    content_seq: int
    content_hash: str
    version_history: list[AssetVersionRef] = field(default_factory=list)
    prerequisites: list[AssetEdgeRef] = field(default_factory=list)
    dependents: list[AssetEdgeRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared legacy-id bridging helpers
# ---------------------------------------------------------------------------


async def _legacy_assets_by_key(
    db: "AsyncSession", lineage_keys: list[str]
) -> dict[str, Asset]:
    """Map each lineage key to its backing legacy ``Asset`` (one batched query)."""
    if not lineage_keys:
        return {}
    rows = (
        (await db.execute(select(Asset).where(Asset.key.in_(lineage_keys))))
        .scalars()
        .all()
    )
    return {a.key: a for a in rows}


async def _latest_semver_status_by_key(
    db: "AsyncSession", asset_by_key: dict[str, Asset]
) -> dict[str, tuple[str | None, LifecycleStatus | None]]:
    """Map each lineage key to ``(latest_semver, status)`` from legacy AssetVersions.

    The "latest" row is the one with the max ``created_at`` per asset — the same
    selection ``app/routers/graph.py`` and the back-fill use. One batched query.
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
        (
            await db.execute(
                select(AssetVersion).join(
                    latest_subq,
                    (AssetVersion.asset_id == latest_subq.c.asset_id)
                    & (AssetVersion.created_at == latest_subq.c.max_ts),
                )
            )
        )
        .scalars()
        .all()
    )
    latest_by_asset_id = {av.asset_id: av for av in rows}

    out: dict[str, tuple[str | None, LifecycleStatus | None]] = {}
    for key, asset in asset_by_key.items():
        av = latest_by_asset_id.get(asset.id)
        if av is None:
            out[key] = (None, None)
        else:
            out[key] = (f"{av.major}.{av.minor}.{av.patch}", av.status)
    return out


# ---------------------------------------------------------------------------
# Calendar / course view
# ---------------------------------------------------------------------------


async def course_calendar(
    db: "AsyncSession", curriculum_id: uuid.UUID
) -> list[CalendarSection]:
    """Build the calendar/course view for a curriculum's active version.

    Resolves the active manifest, groups its members by ``(week_index, section)``
    ordered by ``week_index`` then ``order``, and emits a tile per member. Each
    tile carries the **legacy ``Asset.id``** (so it is clickable into the same
    asset the graph navigates), ``lineage_key``, ``kind``, friendly ``label``,
    ``source_url`` (from the LineageAsset), latest semver + status (from legacy),
    and a ``misaligned`` flag (from ``manifest_alignment``).

    Returns ``[]`` if the curriculum has no back-filled manifest (graceful — the
    frontend simply shows an empty course).
    """
    cversion = await active_curriculum_version(db, curriculum_id)
    if cversion is None:
        return []

    members = await version_members(db, cversion.id)
    if not members:
        return []

    misaligned_lineage_ids = await manifest_alignment(db, cversion.id)

    # Bridge lineage assets -> legacy assets via the shared stable key.
    lineage_keys = [m.lineage_key for m in members]
    asset_by_key = await _legacy_assets_by_key(db, lineage_keys)
    legacy_asset_ids = [a.id for a in asset_by_key.values()]

    label_by_asset_id = await asset_display_names(db, legacy_asset_ids)
    semver_status_by_key = await _latest_semver_status_by_key(db, asset_by_key)

    # source_url lives on the LineageAsset; fetch them by id for these members.
    source_url_by_lineage_id = await _source_urls_by_lineage_id(
        db, [m.asset_id for m in members]
    )

    # version_members is already ordered by (week_index, order, lineage_key), so a
    # single pass preserves the required ordering within and across sections.
    sections: list[CalendarSection] = []
    current_key: tuple[int, str] | None = None
    current_tiles: list[CalendarTile] = []

    def _flush() -> None:
        if current_key is not None:
            sections.append(
                CalendarSection(
                    week_index=current_key[0],
                    section=current_key[1],
                    tiles=current_tiles,
                )
            )

    for m in members:
        asset = asset_by_key.get(m.lineage_key)
        legacy_id = asset.id if asset is not None else m.asset_id
        semver, status = semver_status_by_key.get(m.lineage_key, (None, None))
        tile = CalendarTile(
            id=legacy_id,
            lineage_key=m.lineage_key,
            kind=m.kind,
            label=label_by_asset_id.get(legacy_id, m.lineage_key),
            source_url=source_url_by_lineage_id.get(m.asset_id),
            latest_version=semver,
            status=status,
            misaligned=m.asset_id in misaligned_lineage_ids,
        )
        key = (m.week_index, m.section)
        if key != current_key:
            _flush()
            current_key = key
            current_tiles = []
        current_tiles.append(tile)

    _flush()
    return sections


async def _source_urls_by_lineage_id(
    db: "AsyncSession", lineage_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """Batched ``LineageAsset.id -> source_url`` lookup."""
    if not lineage_ids:
        return {}
    rows = (
        await db.execute(
            select(LineageAsset.id, LineageAsset.source_url).where(
                LineageAsset.id.in_(lineage_ids)
            )
        )
    ).all()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Asset detail
# ---------------------------------------------------------------------------


async def _lineage_for_legacy_asset(
    db: "AsyncSession", asset_id: uuid.UUID
) -> tuple[Asset, LineageAsset] | None:
    """Resolve a **legacy** ``Asset.id`` to its (Asset, LineageAsset) pair.

    The bridge is the shared stable key (``Asset.key == LineageAsset.lineage_key``).
    Returns ``None`` if either side is missing.
    """
    asset = await db.scalar(select(Asset).where(Asset.id == asset_id))
    if asset is None:
        return None
    lineage = await db.scalar(
        select(LineageAsset).where(LineageAsset.lineage_key == asset.key)
    )
    if lineage is None:
        return None
    return asset, lineage


async def asset_detail(
    db: "AsyncSession", asset_id: uuid.UUID
) -> AssetDetail | None:
    """Build the detail view for a single asset, addressed by **legacy Asset.id**.

    Returns the asset's kind/label/source_url, its **selected content body** (the
    active version's member's ``ContentVersion``) with metadata/seq/hash, the full
    append-only **version history** (the LineageAsset's ``ContentVersion`` chain,
    ordered by ``seq``), and its **prerequisites** (incoming edges) and
    **dependents** (outgoing edges) in the active version, each mapped back to a
    legacy id + friendly label.

    Returns ``None`` (the router raises 404) when the asset, its lineage, the
    active manifest, or the asset's membership in that manifest is missing.
    """
    pair = await _lineage_for_legacy_asset(db, asset_id)
    if pair is None:
        return None
    asset, lineage = pair

    # Which curriculum version selected this content? We resolve the active
    # manifest from the *member* placement — find the member for this lineage in
    # any active manifest the asset belongs to. The course browser is curriculum-
    # scoped, but the asset-detail route is addressed by asset id alone, so we
    # locate the member via the lineage's selected content directly.
    member_row = (
        await db.execute(
            select(VersionMember, ContentVersion)
            .join(
                ContentVersion,
                VersionMember.asset_version_id == ContentVersion.id,
            )
            .where(VersionMember.asset_id == lineage.id)
        )
    ).first()
    if member_row is None:
        return None
    member, selected_content = member_row

    label = (await asset_display_names(db, [asset.id])).get(asset.id, asset.key)

    # Full append-only history chain (ordered by seq ascending).
    history_rows = (
        (
            await db.execute(
                select(ContentVersion)
                .where(ContentVersion.asset_id == lineage.id)
                .order_by(ContentVersion.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    version_history = [
        AssetVersionRef(
            seq=cv.seq, content_hash=cv.content_hash, created_at=cv.created_at
        )
        for cv in history_rows
    ]

    prerequisites, dependents = await _asset_relations(
        db, member.curriculum_version_id, lineage.id
    )

    return AssetDetail(
        id=asset.id,
        lineage_key=lineage.lineage_key,
        kind=lineage.kind,
        label=label,
        source_url=lineage.source_url,
        content=selected_content.content,
        content_metadata=selected_content.metadata_,
        content_seq=selected_content.seq,
        content_hash=selected_content.content_hash,
        version_history=version_history,
        prerequisites=prerequisites,
        dependents=dependents,
    )


async def _asset_relations(
    db: "AsyncSession", curriculum_version_id: uuid.UUID, lineage_id: uuid.UUID
) -> tuple[list[AssetEdgeRef], list[AssetEdgeRef]]:
    """Resolve a lineage asset's prerequisites + dependents in a version.

    Prerequisites = incoming edges (``to_asset_id == lineage_id``): the assets this
    one depends on. Dependents = outgoing edges (``from_asset_id == lineage_id``):
    the assets that depend on this one. Each related lineage id is mapped to a
    legacy ``Asset.id`` + friendly label via the shared key.
    """
    edges = await version_edges(db, curriculum_version_id)

    prereq_lineage = [
        (e.from_asset_id, e.edge_type) for e in edges if e.to_asset_id == lineage_id
    ]
    dependent_lineage = [
        (e.to_asset_id, e.edge_type) for e in edges if e.from_asset_id == lineage_id
    ]

    related_lineage_ids = {lid for lid, _ in prereq_lineage} | {
        lid for lid, _ in dependent_lineage
    }
    if not related_lineage_ids:
        return [], []

    # lineage id -> (lineage_key, kind) for the related assets.
    lineage_rows = (
        (
            await db.execute(
                select(LineageAsset).where(LineageAsset.id.in_(related_lineage_ids))
            )
        )
        .scalars()
        .all()
    )
    lineage_by_id = {la.id: la for la in lineage_rows}

    # Bridge to legacy assets + labels.
    keys = [la.lineage_key for la in lineage_rows]
    asset_by_key = await _legacy_assets_by_key(db, keys)
    label_by_asset_id = await asset_display_names(
        db, [a.id for a in asset_by_key.values()]
    )

    def _ref(lineage_id_: uuid.UUID, edge_type: str) -> AssetEdgeRef | None:
        la = lineage_by_id.get(lineage_id_)
        if la is None:
            return None
        asset = asset_by_key.get(la.lineage_key)
        legacy_id = asset.id if asset is not None else lineage_id_
        return AssetEdgeRef(
            id=legacy_id,
            lineage_key=la.lineage_key,
            label=label_by_asset_id.get(legacy_id, la.lineage_key),
            edge_type=edge_type,
        )

    prerequisites = [
        ref
        for lid, et in prereq_lineage
        if (ref := _ref(lid, et)) is not None
    ]
    dependents = [
        ref
        for lid, et in dependent_lineage
        if (ref := _ref(lid, et)) is not None
    ]
    return prerequisites, dependents


# ---------------------------------------------------------------------------
# Mutation: set/clear the editable source link
# ---------------------------------------------------------------------------


async def set_source_url(
    db: "AsyncSession", asset_id: uuid.UUID, source_url: str | None
) -> LineageAsset | None:
    """Set (or clear) the ``source_url`` on the LineageAsset behind a legacy asset.

    Addressed by **legacy ``Asset.id``** (the navigable id). Maps it to its
    ``LineageAsset`` via the shared key, sets ``source_url``, and ``flush``es so the
    change is visible to a follow-up read in the same transaction. The caller
    commits. Returns ``None`` (router raises 404) if the asset/lineage is missing.
    """
    pair = await _lineage_for_legacy_asset(db, asset_id)
    if pair is None:
        return None
    _asset, lineage = pair
    lineage.source_url = source_url
    await db.flush()
    return lineage
