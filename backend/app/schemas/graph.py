"""Pydantic v2 schemas for the curriculum dependency graph endpoint."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.enums import AssetKind, LifecycleStatus


class GraphNode(BaseModel):
    id: uuid.UUID
    kind: AssetKind
    label: str
    latest_version: str | None  # semver string e.g. "1.2.3", or null
    status: LifecycleStatus | None  # lifecycle status of the latest AssetVersion


class GraphEdge(BaseModel):
    from_asset_id: uuid.UUID
    to_asset_id: uuid.UUID
    edge_type: str


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    misaligned_asset_ids: list[uuid.UUID]
