"""Global AI-call telemetry model — persisted per-call usage records.

``AICallEvent`` is durable, GLOBAL telemetry: one row per AI call made through
``AIClient._parse``, written best-effort off the hot path. Unlike the domain
tables it is intentionally NOT tenant-scoped / NOT RLS'd — it's cross-tenant
operational telemetry (spend, latency, token usage) for staff. It carries a
plain, indexed, nullable ``organization_id`` for org attribution, but NO foreign
key: telemetry stays decoupled from the org lifecycle (an org delete must never
cascade-wipe its spend history, and a write must never block on an FK lookup).

Because it is a plain ``Base`` model (not ``TenantScoped``), it does NOT get an
``organization_id`` column default from ``require_org()`` — the writer reads the
org from the ``current_org`` ContextVar explicitly and may legitimately leave it
None for non-tenant calls.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class AICallEvent(Base):
    __tablename__ = "ai_call_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Plain indexed nullable column — NO FK (telemetry stays decoupled).
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # The structured-output type name, e.g. "AdviceReport".
    feature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # Composite index for the org-scoped time-series rollups (by_day, totals).
        Index("ix_ai_call_events_org_created", "organization_id", "created_at"),
    )
