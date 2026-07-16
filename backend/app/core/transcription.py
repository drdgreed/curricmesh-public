"""Media transcription pipeline (Phase B, B2).

``transcribe_asset`` turns a ready ``MediaAsset`` into a stored
``MediaTranscript``:

- ``video`` / ``audio`` → fetch the bytes via the storage adapter and run them
  through the injected ``Transcriber`` (Whisper-class hosted in prod;
  ``FakeTranscriber`` in CI — ZERO real ASR calls).
- ``pdf`` / ``doc`` → direct text extraction (no ASR). See ``_extract_text``:
  UTF-8-decodable bytes (plain-text / markdown docs) are extracted directly;
  binary PDF parsing needs a dedicated library (e.g. ``pypdf``) which is NOT
  bundled — such assets raise ``TranscriptionError`` with guidance.
- ``image`` / ``other`` → skipped (returns ``None``; nothing to transcribe).

The function is IDEMPOTENT: one transcript per asset. A second call is a no-op
that returns the existing transcript, unless ``force=True`` (re-transcribe),
which replaces the stored text in place.

Tenant isolation: the ``MediaAsset`` lookup is org-scoped by the app-layer
``TenantScoped`` auto-filter, so an asset in another org is invisible →
``MediaAssetNotFound`` (the thin admin trigger maps that to HTTP 404).

Downstream chunk+embed into the vector index is a LATER step — this module
only produces and stores transcripts.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media.storage import StorageBackend
from app.media.transcription import Transcriber
from app.models.media import MediaAsset, MediaTranscript

# Media kinds that go through ASR vs. direct text extraction.
_AV_KINDS = frozenset({"video", "audio"})
_TEXT_KINDS = frozenset({"pdf", "doc"})

# Provenance label for directly-extracted (non-ASR) text.
_TEXT_EXTRACT_PROVIDER = "text-extract"


class MediaAssetNotFound(LookupError):
    """Raised when the asset does not exist in the caller's org (→ 404)."""


class TranscriptionError(RuntimeError):
    """Raised when an asset cannot be transcribed/extracted (bad state/format)."""


def _extract_text(kind: str, mime: str, data: bytes) -> str:
    """Directly extract text from a pdf/doc asset (no ASR).

    v1 supports UTF-8-decodable bytes (plain-text / markdown documents). Binary
    PDF parsing requires a dedicated parser library (e.g. ``pypdf``) that is not
    a bundled dependency; a binary payload raises ``TranscriptionError`` naming
    the missing capability rather than storing garbage.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(
            f"cannot extract text from {kind} asset ({mime}): payload is not "
            "UTF-8 text. Binary PDF/doc extraction requires a parser library "
            "(e.g. pypdf), which is not bundled — install it and extend "
            "app.core.transcription._extract_text to enable it."
        ) from exc
    if not text.strip():
        raise TranscriptionError(f"no extractable text in {kind} asset")
    return text


async def transcribe_asset(
    session: AsyncSession,
    asset_id,
    transcriber: Transcriber,
    storage: StorageBackend,
    *,
    force: bool = False,
) -> MediaTranscript | None:
    """Transcribe / extract text for a media asset and store the transcript.

    Returns the stored ``MediaTranscript``, or ``None`` when the asset kind is
    not transcribable (``image`` / ``other``).

    Idempotent: an existing transcript is returned unchanged unless
    ``force=True``, in which case its text/language/provider are replaced.

    Raises:
        MediaAssetNotFound: the asset is not in the caller's org.
        TranscriptionError: the asset is not ready or its bytes can't be
            extracted (e.g. binary PDF without a parser lib).
    """
    asset = (
        await session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None:
        raise MediaAssetNotFound(str(asset_id))

    # Non-transcribable kinds are a clean skip (not an error).
    if asset.kind not in _AV_KINDS and asset.kind not in _TEXT_KINDS:
        return None

    existing = (
        await session.execute(
            select(MediaTranscript).where(
                MediaTranscript.media_asset_id == asset.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None and not force:
        return existing  # idempotent no-op

    if asset.status != "ready":
        raise TranscriptionError(
            f"asset {asset.id} is not ready (status={asset.status})"
        )

    data = storage.fetch(asset.storage_key)

    if asset.kind in _AV_KINDS:
        result = transcriber.transcribe(data)
        text = result.text
        language = result.language
        provider = getattr(transcriber, "provider", "unknown")
    else:  # pdf / doc
        text = _extract_text(asset.kind, asset.mime, data)
        language = None
        provider = _TEXT_EXTRACT_PROVIDER

    if existing is not None:
        # force=True → replace in place (one transcript per asset).
        existing.text = text
        existing.language = language
        existing.provider = provider
        transcript = existing
    else:
        transcript = MediaTranscript(
            media_asset_id=asset.id,
            text=text,
            language=language,
            provider=provider,
        )
        session.add(transcript)

    await session.commit()
    await session.refresh(transcript)
    return transcript
