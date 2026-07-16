"""Immutable, content-addressed version model (foundation — additive).

These five tables sit **alongside** the legacy ``assets``/``asset_versions``/
``versions`` model during the strangler migration; nothing reads them yet. They
implement the git-shaped target of
``docs/specs/2026-06-06-immutable-version-model-design.md`` §3:

* ``LineageAsset`` — a version-independent logical asset (kind + stable key).
* ``ContentVersion`` — an **immutable, append-only** content blob addressed by
  ``content_hash``; one row per revision of a lineage asset's body.
* ``CurriculumVersion`` — a lightweight manifest (semver + status + fork parent).
* ``VersionMember`` — which lineage asset (and *which* content revision) is in a
  curriculum version, plus its placement.
* ``VersionEdge`` — a version-scoped prerequisite edge on *logical* assets, with
  optional ``validated_against_seq`` provenance.

Provisional class/table names (``Lineage*``/``Content*``/``content_versions``)
are intentionally distinct from the live ``assets``/``asset_versions`` tables to
avoid a clash during the strangler; the final rename happens at M3.

All five are ``TenantScoped`` and join the RLS regime (see ``app/db/rls.py`` and
the ``..._immutable_content_model`` migration). Style mirrors
``app/models/structure.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped
from app.models.enums import AssetKind, LifecycleStatus


class LineageAsset(TenantScoped, Base):
    """A version-independent logical asset: a stable lineage (kind + key)."""

    __tablename__ = "lineage_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[AssetKind] = mapped_column(nullable=False)
    lineage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ContentVersion(TenantScoped, Base):
    """An immutable, append-only content blob addressed by ``content_hash``.

    Never UPDATEd — a new revision is a new row (``seq`` monotonic per asset).
    Enforced by the ``before_update`` guard registered at the bottom of this
    module *and* by the application's append-only write path.
    """

    __tablename__ = "content_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "seq", name="uq_content_versions_asset_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # Frozen media pins (Authoring slice 2). On release, publish snapshots the
    # media assets an item references (id + storage_key + kind + filename) into
    # this list so a released version always renders the exact assets it shipped
    # with — a later re-upload is a new asset and never mutates this row (the
    # append-only ContentVersion guard makes the pin immutable by construction).
    media_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class CurriculumVersion(TenantScoped, Base):
    """A lightweight manifest: semver + lifecycle status + fork lineage."""

    __tablename__ = "curriculum_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    major: Mapped[int] = mapped_column(Integer, nullable=False)
    minor: Mapped[int] = mapped_column(Integer, nullable=False)
    patch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LifecycleStatus] = mapped_column(
        nullable=False, default=LifecycleStatus.draft
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VersionMember(TenantScoped, Base):
    """Which lineage asset (and which content revision) is in a version + where."""

    __tablename__ = "version_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(255), nullable=False)
    week_index: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)


class VersionEdge(TenantScoped, Base):
    """A version-scoped prerequisite edge on *logical* assets (never remapped)."""

    __tablename__ = "version_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lineage_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(128), nullable=False)
    validated_against_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Immutability guard — ContentVersion is append-only (never UPDATEd).
# ---------------------------------------------------------------------------


class ImmutableContentVersionError(Exception):
    """Raised when an already-persisted ``ContentVersion`` is mutated.

    Content versions are immutable, content-addressed blobs: a new revision is a
    new row, not an edit. This enforces append-only *in the data layer* (P-009's
    "by construction" discipline), not just by convention.
    """


@event.listens_for(ContentVersion, "before_update")
def _block_content_version_update(mapper, connection, target) -> None:  # noqa: ANN001
    """Refuse any UPDATE against a ``content_versions`` row."""
    raise ImmutableContentVersionError(
        "ContentVersion is immutable (append-only): write a new row with the "
        "next seq instead of editing an existing content version."
    )
