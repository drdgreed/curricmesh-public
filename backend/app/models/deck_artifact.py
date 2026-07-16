"""Deck-artifact linkage model (Slide System Port, S4 — deck ↔ course).

S1 renders a deck to three R2 artifacts (``deck.{pdf,pptx,html}``) under a
tenant-scoped key prefix, but nothing links those keys to a course, so the
Player can't find them. ``DeckArtifact`` closes that gap: one row binds a
rendered deck's three storage keys to a **released** ``CurriculumVersion``
(optionally to the specific ``VersionMember`` it was generated from), so the
learner-facing serve endpoint can list a version's decks and hand back fresh
presigned GET URLs.

``DeckArtifact`` is ``TenantScoped`` and joins the fail-closed RLS regime (see
``app/db/rls.py`` and the ``..._deck_artifacts`` migration). ``status`` is a
plain ``String`` (not a native PG enum) so the test harness's drop/recreate
cycle stays simple — mirrors ``app/models/learner.py``. The three ``*_key``
columns hold storage keys (never presigned URLs — those are minted fresh at
serve time so they never go stale).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class DeckArtifact(TenantScoped, Base):
    """A rendered deck's R2 artifacts linked to a released ``CurriculumVersion``."""

    __tablename__ = "deck_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The item this deck was generated from, when it is item-scoped. NULL for a
    # whole-version deck. SET NULL: dropping the member orphans the deck rather
    # than deleting a still-servable artifact.
    source_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("version_members.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pdf_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    pptx_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    html_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ready"
    )  # ready | pending | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
