"""Pydantic v2 schemas for the course-content browser (Feature A).

Response shapes for the calendar/course view and per-asset detail view.

The id contract mirrors the dependency-graph API: every asset id exposed here is
a **legacy ``Asset.id``** (resolved from the manifest's ``LineageAsset`` via the
shared stable key), so the frontend navigates tiles and graph nodes by the same
ids. See ``app/routers/graph.py``'s ``_graph_from_manifest`` for the mapping.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

from app.models.enums import AssetKind, LifecycleStatus


# ---------------------------------------------------------------------------
# Calendar / course view
# ---------------------------------------------------------------------------


class CalendarTile(BaseModel):
    """A single clickable asset tile in the calendar/course view."""

    id: uuid.UUID  # legacy Asset.id — the navigable id (matches graph nodes)
    lineage_key: str
    kind: AssetKind
    label: str
    source_url: str | None
    latest_version: str | None  # semver e.g. "1.2.3", or null
    status: LifecycleStatus | None
    misaligned: bool


class CalendarSection(BaseModel):
    """A group of tiles sharing a (week_index, section) placement."""

    week_index: int
    section: str
    tiles: list[CalendarTile]


class CourseCalendarOut(BaseModel):
    """The full calendar for a curriculum's active version."""

    curriculum_id: uuid.UUID
    sections: list[CalendarSection]


# ---------------------------------------------------------------------------
# Asset detail
# ---------------------------------------------------------------------------


class AssetVersionRef(BaseModel):
    """One entry in an asset's content-version history chain."""

    seq: int
    content_hash: str
    created_at: datetime


class AssetEdgeRef(BaseModel):
    """A prerequisite or dependent of an asset (legacy-id + label + edge_type)."""

    id: uuid.UUID  # legacy Asset.id of the related asset
    lineage_key: str
    label: str
    edge_type: str


class AssetDetailOut(BaseModel):
    """The full detail view for a single asset (its selected content + relations)."""

    id: uuid.UUID  # legacy Asset.id (the id requested)
    lineage_key: str
    kind: AssetKind
    label: str
    source_url: str | None
    # Selected content (the active version's member's ContentVersion).
    content: str
    content_metadata: dict | None
    content_seq: int
    content_hash: str
    # Full append-only history chain (ordered by seq ascending).
    version_history: list[AssetVersionRef]
    # Logical relations resolved to legacy ids.
    prerequisites: list[AssetEdgeRef]  # incoming edges (assets this one needs)
    dependents: list[AssetEdgeRef]  # outgoing edges (assets that need this one)


# ---------------------------------------------------------------------------
# PATCH source-url
# ---------------------------------------------------------------------------


class SourceUrlIn(BaseModel):
    """Request body for setting (or clearing) an asset's editable source link.

    Defense-in-depth against stored XSS: ``source_url`` is rendered as an
    ``<a href>`` in the UI, so a non-``http(s)`` scheme (e.g. ``javascript:``)
    is rejected here (422) — the API is callable directly, so the frontend's
    allowlist alone is insufficient.
    """

    source_url: str | None

    @field_validator("source_url")
    @classmethod
    def _safe_scheme(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        scheme = urlparse(v).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError("source_url must be an http(s) URL")
        return v


class SourceUrlOut(BaseModel):
    """The result of a source-url update."""

    id: uuid.UUID  # legacy Asset.id
    lineage_key: str
    source_url: str | None
