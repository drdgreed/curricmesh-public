"""Version-to-version diff over the immutable manifest (additive, pure read).

Compares two ``CurriculumVersion`` snapshots — their members and edges — and
reports the structural delta between them:

* **assets_added** — lineage assets present in head but not base.
* **assets_removed** — lineage assets present in base but not head.
* **assets_changed** — lineage assets in BOTH whose selected ``asset_version_id``
  differs (a different revision is selected), carrying the from/to ContentVersion
  ``seq`` + ``content_hash`` so the caller can show "rev 2 → rev 3".
* **edges_added** / **edges_removed** — edges (keyed by ``(from, to, edge_type)``
  on logical lineage assets) present in one side but not the other.

Everything is keyed on the stable **lineage asset id** (version-independent), so
the diff is meaningful across the immutable snapshots. Like the rest of
``manifest.py`` this is a pure read over the new content model; the router layer
maps lineage ids → legacy Asset ids + friendly labels for the HTTP contract.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.manifest import ManifestMember, version_edges, version_members

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class AssetChange:
    """A lineage asset present in both versions but pointing at a new revision."""

    asset_id: uuid.UUID         # LineageAsset id
    lineage_key: str
    from_seq: int
    from_hash: str
    to_seq: int
    to_hash: str


@dataclass(frozen=True)
class DiffAsset:
    """A lineage asset added to / removed from a version (with its selected rev)."""

    asset_id: uuid.UUID         # LineageAsset id
    lineage_key: str
    seq: int
    content_hash: str


@dataclass(frozen=True)
class DiffEdge:
    """An edge added to / removed from a version (logical lineage endpoints)."""

    from_asset_id: uuid.UUID
    to_asset_id: uuid.UUID
    edge_type: str


@dataclass(frozen=True)
class VersionDiff:
    """The full structural delta from ``base`` to ``head``."""

    assets_added: list[DiffAsset]
    assets_removed: list[DiffAsset]
    assets_changed: list[AssetChange]
    edges_added: list[DiffEdge]
    edges_removed: list[DiffEdge]


def _diff_asset(m: ManifestMember, content_hash: str) -> DiffAsset:
    return DiffAsset(
        asset_id=m.asset_id,
        lineage_key=m.lineage_key,
        seq=m.content_seq,
        content_hash=content_hash,
    )


async def version_diff(
    session: "AsyncSession", base_cv_id: uuid.UUID, head_cv_id: uuid.UUID
) -> VersionDiff:
    """Diff two curriculum versions' members + edges (base → head).

    Pure read over the manifest layer. Members are matched by lineage asset id;
    edges by ``(from_asset_id, to_asset_id, edge_type)``. The content hash needed
    for the asset deltas comes from a single batched ContentVersion lookup.
    """
    base_members = await version_members(session, base_cv_id)
    head_members = await version_members(session, head_cv_id)

    base_by_id = {m.asset_id: m for m in base_members}
    head_by_id = {m.asset_id: m for m in head_members}

    # Batched content_hash for every selected ContentVersion across both sides.
    hash_by_cv_id = await _content_hashes(
        session,
        [m.content_version_id for m in base_members]
        + [m.content_version_id for m in head_members],
    )

    def _hash(m: ManifestMember) -> str:
        return hash_by_cv_id.get(m.content_version_id, "")

    assets_added = [
        _diff_asset(m, _hash(m))
        for aid, m in head_by_id.items()
        if aid not in base_by_id
    ]
    assets_removed = [
        _diff_asset(m, _hash(m))
        for aid, m in base_by_id.items()
        if aid not in head_by_id
    ]
    assets_changed = [
        AssetChange(
            asset_id=aid,
            lineage_key=head_by_id[aid].lineage_key,
            from_seq=base_by_id[aid].content_seq,
            from_hash=_hash(base_by_id[aid]),
            to_seq=head_by_id[aid].content_seq,
            to_hash=_hash(head_by_id[aid]),
        )
        for aid in base_by_id.keys() & head_by_id.keys()
        if base_by_id[aid].content_version_id != head_by_id[aid].content_version_id
    ]

    base_edges = await version_edges(session, base_cv_id)
    head_edges = await version_edges(session, head_cv_id)

    def _edge_key(e) -> tuple[uuid.UUID, uuid.UUID, str]:
        return (e.from_asset_id, e.to_asset_id, e.edge_type)

    base_edge_keys = {_edge_key(e) for e in base_edges}
    head_edge_keys = {_edge_key(e) for e in head_edges}

    edges_added = [
        DiffEdge(from_asset_id=e.from_asset_id, to_asset_id=e.to_asset_id, edge_type=e.edge_type)
        for e in head_edges
        if _edge_key(e) not in base_edge_keys
    ]
    edges_removed = [
        DiffEdge(from_asset_id=e.from_asset_id, to_asset_id=e.to_asset_id, edge_type=e.edge_type)
        for e in base_edges
        if _edge_key(e) not in head_edge_keys
    ]

    return VersionDiff(
        assets_added=assets_added,
        assets_removed=assets_removed,
        assets_changed=assets_changed,
        edges_added=edges_added,
        edges_removed=edges_removed,
    )


async def _content_hashes(
    session: "AsyncSession", content_version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """``{ContentVersion.id: content_hash}`` for the given ids (one batched query)."""
    from sqlalchemy import select

    from app.models.content_model import ContentVersion

    ids = list(set(content_version_ids))
    if not ids:
        return {}
    return dict(
        (
            await session.execute(
                select(ContentVersion.id, ContentVersion.content_hash).where(
                    ContentVersion.id.in_(ids)
                )
            )
        ).all()
    )
