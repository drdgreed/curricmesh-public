"""MediaAsset model — owned media files for the Authoring Platform.

Tenant-scoped via TenantScoped mixin; every row carries ``organization_id``
stamped from the ambient org context (fail-closed: require_org() raises if
unset).

Design notes:
- ``status`` lifecycle: pending → ready (on confirm) | failed.
- ``size_bytes`` and ``checksum`` are NULL until the client confirms upload
  (the server never proxies bytes; client PUTs directly to storage).
- ``checksum`` is the client-reported sha256 (content-address trust v1:
  authors are trusted, tenant-scoped; full server-side verification deferred).
- ``duration_s`` is optional (audio/video only).
- ``storage_key`` is the object key in the configured bucket (never NULL:
  the backend generates it at upload-url time before creating the row).
- v1: no unique constraint on storage_key (uniqueness is ensured by the UUID
  component of the key at generation time; DB enforcement deferred to slice 2).

Migration: revises c4e6f8a0b2d4 (see alembic/versions/<newid>_media_assets.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class MediaAsset(TenantScoped, Base):
    """A tenant-owned media file (video, audio, image, pdf, doc, or other)."""

    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # video | audio | image | pdf | doc | other
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # set at confirm
    checksum: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # client sha256, set at confirm
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)  # object key
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending | ready | failed
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaTranscript(TenantScoped, Base):
    """Extracted/transcribed text for a media asset (Phase B, B2).

    One transcript per asset (``media_asset_id`` is UNIQUE); a re-transcribe
    replaces the existing row rather than appending. Tenant-scoped via the
    ``TenantScoped`` mixin (``organization_id`` write-stamped from the ambient
    org context) → joins the same fail-closed RLS regime as ``media_assets``.

    ``provider`` records how the text was produced (e.g. a Whisper-class model
    id for AV, or ``"text-extract"`` for PDF/doc direct extraction). ``text`` is
    the full transcript; downstream (a LATER step) chunks + embeds it into the
    vector index — this table only stores the transcript.

    Migration: revises a3b5c7d9e1f2 (see alembic/versions/<id>_media_transcripts.py).
    """

    __tablename__ = "media_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one transcript per asset (re-transcribe replaces)
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
