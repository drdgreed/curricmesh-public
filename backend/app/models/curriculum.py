"""Curriculum model — the top-level unit of version control."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class Curriculum(TenantScoped, Base):
    __tablename__ = "curricula"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Points to the currently-active Version; nullable until first version is published.
    # Note: no DB-level FK here to avoid a circular dependency between curricula ↔ versions.
    # Referential integrity is enforced at the application layer (A3+).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # New-model active pointer (M4 fork): the live immutable CurriculumVersion.
    # Nullable — NULL means "fall back to the legacy semver bridge" (back-filled
    # curricula), so adding this is fully backward compatible. ``fork()`` sets it
    # via an optimistic compare-and-swap on activation.
    #
    # Like ``current_version_id`` above, this carries NO DB-level FK: a real FK
    # would close a cycle (curriculum_versions.curriculum_id -> curricula.id and
    # back), which breaks metadata-level create_all/drop_all (used by the test
    # harness). Referential integrity is enforced at the application layer (fork
    # only ever points this at a CurriculumVersion it just created).
    active_content_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
