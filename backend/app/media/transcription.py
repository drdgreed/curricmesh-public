"""Transcription-provider abstraction for owned audio/video media.

Design (mirrors ``app/media/storage.py``):

- ``Transcriber`` is a ``@runtime_checkable`` Protocol; any class implementing
  ``transcribe(audio_ref) -> TranscriptResult`` is a valid provider without
  explicit inheritance.
- ``FakeTranscriber`` — deterministic, in-memory; used by tests (ZERO network,
  no API key). Same output for the same input bytes.
- ``HostedTranscriber`` — a Whisper-class hosted provider hitting an
  OpenAI-compatible ``/audio/transcriptions`` endpoint. The real network POST
  happens ONLY inside ``transcribe`` and is NEVER exercised in CI.
- ``get_transcriber()`` — dependency. Returns ``HostedTranscriber`` when
  ``settings.TRANSCRIBE_API_KEY`` is set; raises ``HTTPException(503)``
  otherwise, mirroring the ``get_storage`` (STORAGE_BUCKET-disabled) pattern.

  Tests inject the Fake directly (the pipeline takes a ``Transcriber`` param) or
  via ``app.dependency_overrides[get_transcriber]``.

No ``temperature``/provider-specific tuning is exposed (YAGNI): the hosted call
sends the audio bytes + model id and reads back ``text``/``duration``/``language``.
"""

from __future__ import annotations

import io
from typing import Protocol, runtime_checkable

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from app.config import settings


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class TranscriptResult(BaseModel):
    """The output of a transcription call.

    ``duration_s`` and ``language`` are provider-optional (Whisper returns them
    on a verbose response; a minimal response may omit them).
    """

    text: str
    duration_s: float | None = None
    language: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Transcriber(Protocol):
    """Minimal ASR interface consumed by the transcription pipeline.

    ``provider`` labels how the text was produced (recorded on the stored
    transcript for provenance — e.g. a model id).
    """

    provider: str

    def transcribe(self, audio_ref: bytes) -> TranscriptResult:
        """Transcribe raw audio/video bytes to text.

        ``audio_ref`` is the object's bytes (fetched via the storage adapter).
        """
        ...


# ---------------------------------------------------------------------------
# FakeTranscriber (tests only)
# ---------------------------------------------------------------------------


class FakeTranscriber:
    """Deterministic in-memory transcriber for tests (no network, no API key).

    The same input bytes always yield the same ``TranscriptResult`` so tests
    can assert idempotency. Pass a fixed ``text`` to control the output exactly.
    """

    provider = "fake"

    def __init__(self, text: str | None = None, language: str = "en") -> None:
        self._text = text
        self._language = language

    def transcribe(self, audio_ref: bytes) -> TranscriptResult:
        text = (
            self._text
            if self._text is not None
            else f"fake transcript of {len(audio_ref)} bytes"
        )
        return TranscriptResult(
            text=text,
            duration_s=float(len(audio_ref)),
            language=self._language,
        )


# ---------------------------------------------------------------------------
# HostedTranscriber (production — Whisper-class, OpenAI-compatible)
# ---------------------------------------------------------------------------


class HostedTranscriber:
    """Hosted Whisper-class transcriber via an OpenAI-compatible ASR endpoint.

    Construction is inert (no network). The real ``httpx.post`` fires only when
    ``transcribe`` is called — which never happens in CI (FakeTranscriber is
    injected there). Errors are NOT swallowed; they propagate to the caller.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        endpoint_url: str,
        timeout_s: float = 300.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint_url
        self._timeout = timeout_s
        # Provenance label recorded on the stored transcript (the model id).
        self.provider = model

    def transcribe(self, audio_ref: bytes) -> TranscriptResult:
        resp = httpx.post(
            self._endpoint,
            headers={"Authorization": f"Bearer {self._api_key}"},
            files={"file": ("audio", io.BytesIO(audio_ref))},
            data={"model": self._model, "response_format": "verbose_json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return TranscriptResult(
            text=data.get("text", ""),
            duration_s=data.get("duration"),
            language=data.get("language"),
        )


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_transcriber() -> Transcriber:
    """Return the configured transcription provider.

    Raises ``HTTPException(503)`` when ``TRANSCRIBE_API_KEY`` is empty, mirroring
    the storage-disabled (STORAGE_BUCKET) pattern. Tests inject the Fake via::

        app.dependency_overrides[get_transcriber] = lambda: FakeTranscriber()
    """
    if not settings.TRANSCRIBE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Media transcription is not configured. "
                "Set TRANSCRIBE_API_KEY (and related TRANSCRIBE_* vars) to enable."
            ),
        )
    return HostedTranscriber(
        api_key=settings.TRANSCRIBE_API_KEY,
        model=settings.TRANSCRIBE_MODEL,
        endpoint_url=settings.TRANSCRIBE_ENDPOINT_URL,
        timeout_s=settings.TRANSCRIBE_TIMEOUT_S,
    )
