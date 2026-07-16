"""Schemas for the executable-release endpoint (Phase C).

A *release* applies a structured change-set to a curriculum's currently-active
manifest by calling :func:`app.core.fork.fork`, producing + activating a new
immutable :class:`~app.models.content_model.CurriculumVersion`. These models are
the HTTP surface that the rich-CCR-authoring UI (Feature B) POSTs to when a
change request is merged. They map 1:1 onto the ``fork()`` change-set dataclasses
(``ContentEdit`` / ``NewAsset`` / ``EdgeSpec`` / ``ForkChanges``).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.fork import Bump
from app.models.enums import AssetKind


class ContentEditIn(BaseModel):
    """Edit the selected content of an asset already in the active version."""

    lineage_key: str
    content: str | None = None
    metadata: dict | None = None
    # Optional placement move (kept null = unchanged).
    section: str | None = None
    week_index: int | None = None
    order: int | None = None


class NewAssetIn(BaseModel):
    """Add a brand-new asset to the new version (with its initial content)."""

    lineage_key: str
    kind: AssetKind
    content: str | None = None
    metadata: dict | None = None
    section: str = ""
    week_index: int = 0
    order: int = 0
    source_url: str | None = None


class EdgeSpecIn(BaseModel):
    """A prerequisite edge (``from_key`` prerequisite → ``to_key`` dependent)."""

    from_key: str
    to_key: str
    edge_type: str = "prerequisite"
    validated_against_seq: int | None = None


class ReleaseChangeSet(BaseModel):
    """The structured executable change-set persisted on a ChangeRequest.

    This is the ``ReleaseRequest`` shape minus the transport-only fields
    (``expected_active_id`` / ``ccr_id`` / ``note``). The PR-style merge endpoint
    stores it on ``ChangeRequest.change_set`` at authoring time and replays it
    through ``fork()`` when the change request is approved and merged. The AI
    CCR-impact endpoint also accepts this shape as the unit it analyzes.
    """

    bump: Bump
    changed: list[ContentEditIn] = Field(default_factory=list)
    added: list[NewAssetIn] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    edges_added: list[EdgeSpecIn] = Field(default_factory=list)
    edges_removed: list[EdgeSpecIn] = Field(default_factory=list)


class ReleaseRequest(ReleaseChangeSet):
    """A structured change-set to apply as a new released version."""

    # Optimistic-concurrency token: the active version id the caller built on.
    # Null = "whatever is active now" (first release on a back-filled curriculum).
    expected_active_id: uuid.UUID | None = None
    # Optional provenance: the merged CCR + a human note for the audit trail.
    ccr_id: uuid.UUID | None = None
    note: str | None = None


class ReleaseSummary(BaseModel):
    changed: int
    added: int
    removed: int
    edges_added: int
    edges_removed: int


class ReleaseGateOut(BaseModel):
    """The release-gate status for a change request (why merge is/isn't unlocked).

    The gate (``can_release``) is: a passing QA review + >= 2 approvals + >= 1
    approval from an instructor role. The component flags let the UI show exactly
    what is still missing.
    """

    has_change_set: bool
    qa_passed: bool
    approval_count: int
    has_instructor_approval: bool
    can_release: bool


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    curriculum_id: uuid.UUID
    version_id: uuid.UUID
    semver: str
    status: str
    parent_version_id: uuid.UUID | None
    member_count: int
    edge_count: int
    summary: ReleaseSummary
