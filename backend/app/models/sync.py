"""Sync models — audit trail + per-tenant outbound-sync config (V3-C / Phase 4).

``SyncLog`` is an immutable record of an external-sync attempt: every attempt
to publish a released curriculum version to an external target (GitHub / LMS)
appends a row.  Failed attempts are logged, never swallowed.

``SyncTarget`` is configuration-as-data: one row per curriculum × kind (e.g.
``github_pr``), carrying the JSONB config the sync service needs (repo,
branch, path mapping).  It is tenant-scoped and RLS-protected.

Both models are tenant-scoped (``TenantScoped``): written with the ambient org
and subject to the same fail-closed RLS regime as the other domain tables.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class SyncTarget(TenantScoped, Base):
    """Per-tenant outbound sync destination for a curriculum's releases."""

    __tablename__ = "sync_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "github_pr" is the only kind implemented in Phase 4.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="github_pr")
    # Sync config. github_pr shape: {repo, base_branch, path_prefix, path_rules?}
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SyncLog(TenantScoped, Base):
    __tablename__ = "sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Legacy Version FK — nullable since Phase 4: new-model releases have no
    # legacy Version row.  Existing callers still pass version_id; new-model
    # callers pass version_id=None and set curriculum_version_id instead.
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Immutable-model Version FK — set by Phase-4 sync service; NULL for legacy
    # sync attempts that predate the content model.
    curriculum_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # "github" | "lms"
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    # "success" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # The provider's SyncResult payload: {"url": ..., "message": ...}.
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
