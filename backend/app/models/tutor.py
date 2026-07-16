"""Tutor conversation store (Phase B, B3 — RAG Q&A tutor).

The **secure server-side record** half of the D5 privacy design. A learner's
tutor turns persist here in FULL — the un-redacted question and the tutor's
grounded answer — while the model itself only ever sees anonymized,
PII-redacted input (see ``app/core/tutor/answer.py``). Identity and content are
separated: these rows carry NO learner id/name/email. Threading is by
``enrollment_id`` (which the backend maps to a learner) — the conversation
itself is an opaque per-enrollment handle, never a user identifier the model
sees.

* ``TutorConversation`` — one chat thread, pinned to an ``Enrollment`` (which
  pins the released ``CurriculumVersion`` the tutor is grounded in).
* ``TutorMessage`` — one turn (``role`` = ``learner`` | ``tutor``), its text,
  and — for tutor turns — the ``citations`` (JSONB) back to the source
  ``ContentChunk``s / their ``source_member_id`` that grounded the answer.

Both are ``TenantScoped`` and join the fail-closed RLS regime (see
``app/db/rls.py`` and the ``..._tutor_conversations`` migration). ``role`` is a
plain ``String`` (not a native PG enum) so the test harness's drop/recreate
cycle stays simple — mirrors ``app/models/learner.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class TutorConversation(TenantScoped, Base):
    """One tutor chat thread, pinned to a learner's ``Enrollment``."""

    __tablename__ = "tutor_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TutorMessage(TenantScoped, Base):
    """One turn in a ``TutorConversation`` — full, un-redacted, server-side."""

    __tablename__ = "tutor_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tutor_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # learner | tutor. Plain String (see module docstring).
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # FULL un-redacted text (server-side record per D5). What the MODEL sees is
    # anonymized + PII-redacted elsewhere; this store is the secure original.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Tutor turns only: list of source citations (ContentChunk / source_member
    # ids + snippet). Null for learner turns.
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
