"""Retrieval infrastructure — ``ContentChunk`` (Phase B, Foundation 1).

A ``ContentChunk`` is one embeddable slice of a **released** curriculum
version's material: the item body text (``kind="text"``) or, later, a media
asset's transcript (``kind="media_transcript"``, populated by the sibling
transcription build). Chunks are **pinned to a curriculum version** — because a
released version is immutable, its chunk set (and therefore the retrieval index
the tutor reads) is stable and reproducible per version.

Design:
* ``TenantScoped`` → carries ``organization_id`` (write-stamped from the ambient
  org context) and joins the fail-closed RLS regime (registered in
  ``app/db/rls.py``); the app-layer auto-filter (``app.tenant_scope``) also
  scopes every SELECT touching this table to the current tenant.
* ``embedding`` is a pgvector ``Vector(settings.EMBEDDING_DIM)`` column. The
  width is fixed at table-creation time (see the migration), so it MUST match
  the configured embedder's output dimension.
* An HNSW ANN index on ``embedding`` with ``vector_cosine_ops`` backs the
  ``<=>`` cosine-distance top-k query in ``app.core.retrieval.retrieve``. HNSW
  needs no training step (unlike IVFFlat), so it is correct on an empty/small
  table — which the test suite relies on.

Style mirrors ``app/models/media.py`` / ``app/models/content_model.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.config import settings
from app.database import Base
from app.models._tenant import TenantScoped


class ContentChunk(TenantScoped, Base):
    """One embeddable, version-pinned slice of a released version's material."""

    __tablename__ = "content_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The released version this chunk indexes. Chunks are dropped + rebuilt
    # per version by the (idempotent) ingest job, never mutated in place.
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The version member whose content produced this chunk. Nullable so a
    # non-member source (e.g. a future standalone transcript) can still index.
    source_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("version_members.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # text | media_transcript
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.EMBEDDING_DIM), nullable=False
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # HNSW ANN index for cosine-distance (``<=>``) top-k retrieval.
        Index(
            "ix_content_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
