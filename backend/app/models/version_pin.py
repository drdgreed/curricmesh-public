"""VersionPin model — per-student immutable provenance pin.

A ``version_pins`` row records that a single student trained on the *exact*
curriculum version they completed, so a portfolio can cite immutable provenance
even after the curriculum keeps evolving. This is finer-grained than
``cohorts.version_id`` (the cohort's *current* version, which can change): a pin
is per-student and durable, and may outlive the cohort it references.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class VersionPin(TenantScoped, Base):
    __tablename__ = "version_pins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Optional link to the cohort the student was part of. SET NULL so deleting a
    # cohort doesn't erase the durable provenance pin.
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )
    student_label: Mapped[str] = mapped_column(String(255), nullable=False)
    student_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # active / graduated / withdrawn
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
