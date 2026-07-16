"""Pydantic v2 schemas for the version-to-version diff endpoint.

Asset deltas carry **legacy Asset ids** + friendly labels (same contract as the
graph endpoint); edge deltas carry endpoint labels + the edge type. Changed
assets carry the from/to selected-revision ``seq`` + ``content_hash`` so the UI
can render "rev 2 → rev 3".
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class DiffAssetOut(BaseModel):
    asset_id: uuid.UUID
    label: str
    seq: int
    content_hash: str


class AssetChangeOut(BaseModel):
    asset_id: uuid.UUID
    label: str
    from_seq: int
    from_hash: str
    to_seq: int
    to_hash: str


class DiffEdgeOut(BaseModel):
    from_label: str
    to_label: str
    edge_type: str


class VersionDiffOut(BaseModel):
    base_version_id: uuid.UUID | None
    head_version_id: uuid.UUID
    assets_added: list[DiffAssetOut]
    assets_removed: list[DiffAssetOut]
    assets_changed: list[AssetChangeOut]
    edges_added: list[DiffEdgeOut]
    edges_removed: list[DiffEdgeOut]


class ActiveVersionOut(BaseModel):
    """The curriculum's active ``CurriculumVersion`` id + its parent.

    Lets the frontend drive the version-diff endpoint for the default
    "what changed in the current version" view — the diff route keys on
    ``CurriculumVersion`` ids, which the legacy ``/versions`` list does not
    expose. ``parent_version_id`` is ``None`` for a root (first) version.
    """

    curriculum_id: uuid.UUID
    head_version_id: uuid.UUID
    parent_version_id: uuid.UUID | None
    semver: str
    status: str
