"""Pydantic v2 schemas for Asset and AssetVersion resources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AssetKind, LifecycleStatus


class AssetCreate(BaseModel):
    kind: AssetKind
    key: str
    module_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: AssetKind
    key: str
    module_id: uuid.UUID | None
    project_id: uuid.UUID | None
    created_at: datetime


class AssetVersionCreate(BaseModel):
    major: int
    minor: int
    patch: int
    body_ref: str | None = None
    metadata_: dict | None = None


class AssetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    major: int
    minor: int
    patch: int
    status: LifecycleStatus  # AssetVersion lifecycle wired in Milestone B
    body_ref: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Diff schemas (Task B4)
# ---------------------------------------------------------------------------


class TextDiffOut(BaseModel):
    """Serialised form of core/diff/service.TextDiff."""

    added: list[str]
    removed: list[str]
    unified: str


class StructuredDiffOut(BaseModel):
    """Serialised form of core/diff/service.StructuredDiff."""

    added: list[Any]
    removed: list[Any]
    changed: list[dict[str, Any]]


class DiffOut(BaseModel):
    """Top-level diff response mirroring core/diff/service.DiffResult."""

    kind: str
    text: TextDiffOut | None = None
    structured: StructuredDiffOut | None = None


# ---------------------------------------------------------------------------
# Asset version list schema (Task B5)
# ---------------------------------------------------------------------------


class AssetVersionListItem(BaseModel):
    """Lightweight projection of AssetVersion for the versions list endpoint."""

    id: uuid.UUID
    semver: str
    status: str
    created_at: datetime
