"""Tests for the transcription pipeline core (app/core/transcription).

Uses ``FakeTranscriber`` + ``FakeStorageBackend`` (ZERO network / ZERO real
ASR). Covers: AV → stored transcript, idempotency, force-replace, pdf/doc text
extraction, binary-pdf error, image/other skip, not-ready + not-found errors,
and the storage adapter's new ``fetch``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.transcription import (
    MediaAssetNotFound,
    TranscriptionError,
    transcribe_asset,
)
from app.media.storage import FakeStorageBackend
from app.media.transcription import FakeTranscriber
from app.models.media import MediaAsset, MediaTranscript
from tests.conftest import DEFAULT_ORG_ID


class _CountingTranscriber(FakeTranscriber):
    """FakeTranscriber that records how many times ``transcribe`` ran."""

    def __init__(self, text: str | None = None) -> None:
        super().__init__(text=text)
        self.calls = 0

    def transcribe(self, audio_ref: bytes):
        self.calls += 1
        return super().transcribe(audio_ref)


async def _make_asset(
    session: AsyncSession,
    storage: FakeStorageBackend,
    *,
    kind: str,
    mime: str,
    data: bytes | None,
    status: str = "ready",
) -> MediaAsset:
    key = f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/f"
    asset = MediaAsset(
        kind=kind, filename="f", mime=mime, storage_key=key, status=status
    )
    session.add(asset)
    await session.flush()
    if data is not None:
        storage.put_bytes(key, data)
    return asset


# ---------------------------------------------------------------------------
# Storage adapter: fetch
# ---------------------------------------------------------------------------


def test_fake_storage_fetch_round_trip():
    s = FakeStorageBackend()
    s.put_bytes("k", b"payload")
    assert s.fetch("k") == b"payload"
    assert s.head("k") == {"size": 7}


def test_fake_storage_fetch_missing_raises():
    with pytest.raises(FileNotFoundError):
        FakeStorageBackend().fetch("nope")


# ---------------------------------------------------------------------------
# AV → transcript
# ---------------------------------------------------------------------------


async def test_transcribe_audio_stores_transcript(db_session: AsyncSession):
    storage = FakeStorageBackend()
    transcriber = FakeTranscriber()
    asset = await _make_asset(
        db_session, storage, kind="audio", mime="audio/mp4", data=b"abcde"
    )

    tr = await transcribe_asset(db_session, asset.id, transcriber, storage)

    assert tr is not None
    assert tr.media_asset_id == asset.id
    assert tr.text == "fake transcript of 5 bytes"
    assert tr.language == "en"
    assert tr.provider == "fake"
    assert tr.organization_id == DEFAULT_ORG_ID
    # Exactly one row persisted.
    count = await db_session.scalar(
        select(func.count()).select_from(MediaTranscript)
    )
    assert count == 1


async def test_transcribe_is_idempotent(db_session: AsyncSession):
    storage = FakeStorageBackend()
    transcriber = _CountingTranscriber()
    asset = await _make_asset(
        db_session, storage, kind="video", mime="video/mp4", data=b"xyz"
    )

    first = await transcribe_asset(db_session, asset.id, transcriber, storage)
    second = await transcribe_asset(db_session, asset.id, transcriber, storage)

    assert first.id == second.id
    assert transcriber.calls == 1, "second call must NOT re-run transcription"
    count = await db_session.scalar(
        select(func.count()).select_from(MediaTranscript)
    )
    assert count == 1


async def test_transcribe_force_replaces_in_place(db_session: AsyncSession):
    storage = FakeStorageBackend()
    asset = await _make_asset(
        db_session, storage, kind="audio", mime="audio/mp4", data=b"aa"
    )

    first = await transcribe_asset(
        db_session, asset.id, FakeTranscriber(text="v1"), storage
    )
    replaced = await transcribe_asset(
        db_session, asset.id, FakeTranscriber(text="v2"), storage, force=True
    )

    assert replaced.id == first.id  # same row (one per asset)
    assert replaced.text == "v2"
    count = await db_session.scalar(
        select(func.count()).select_from(MediaTranscript)
    )
    assert count == 1


# ---------------------------------------------------------------------------
# pdf / doc → direct text extraction (no ASR)
# ---------------------------------------------------------------------------


async def test_transcribe_doc_direct_text_extraction(db_session: AsyncSession):
    storage = FakeStorageBackend()
    transcriber = _CountingTranscriber()
    asset = await _make_asset(
        db_session,
        storage,
        kind="doc",
        mime="text/plain",
        data="Module 1: Agentic AI foundations.".encode("utf-8"),
    )

    tr = await transcribe_asset(db_session, asset.id, transcriber, storage)

    assert tr is not None
    assert tr.text == "Module 1: Agentic AI foundations."
    assert tr.provider == "text-extract"
    assert tr.language is None
    assert transcriber.calls == 0, "text extraction must NOT call the ASR provider"


async def test_transcribe_binary_pdf_raises(db_session: AsyncSession):
    storage = FakeStorageBackend()
    # A binary PDF header — not UTF-8 decodable.
    asset = await _make_asset(
        db_session,
        storage,
        kind="pdf",
        mime="application/pdf",
        data=b"%PDF-1.7\x00\x80\x81\xff binary",
    )
    with pytest.raises(TranscriptionError):
        await transcribe_asset(db_session, asset.id, FakeTranscriber(), storage)


# ---------------------------------------------------------------------------
# image / other → skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["image", "other"])
async def test_transcribe_skips_non_transcribable(
    db_session: AsyncSession, kind: str
):
    storage = FakeStorageBackend()
    asset = await _make_asset(
        db_session, storage, kind=kind, mime="image/png", data=b"\x89PNG"
    )
    result = await transcribe_asset(
        db_session, asset.id, FakeTranscriber(), storage
    )
    assert result is None
    count = await db_session.scalar(
        select(func.count()).select_from(MediaTranscript)
    )
    assert count == 0


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


async def test_transcribe_not_ready_raises(db_session: AsyncSession):
    storage = FakeStorageBackend()
    asset = await _make_asset(
        db_session,
        storage,
        kind="audio",
        mime="audio/mp4",
        data=b"aa",
        status="pending",
    )
    with pytest.raises(TranscriptionError):
        await transcribe_asset(db_session, asset.id, FakeTranscriber(), storage)


async def test_transcribe_missing_asset_raises_not_found(db_session: AsyncSession):
    with pytest.raises(MediaAssetNotFound):
        await transcribe_asset(
            db_session, uuid.uuid4(), FakeTranscriber(), FakeStorageBackend()
        )
