"""Version model — a point-in-time snapshot of a Curriculum."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped
from app.models.enums import LifecycleStatus  # re-exported for import convenience

__all__ = ["Version", "LifecycleStatus"]


class Version(TenantScoped, Base):
    __tablename__ = "versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curricula.id", ondelete="CASCADE"),
        nullable=False,
    )
    major: Mapped[int] = mapped_column(Integer, nullable=False)
    minor: Mapped[int] = mapped_column(Integer, nullable=False)
    patch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LifecycleStatus] = mapped_column(
        nullable=False, default=LifecycleStatus.draft
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
