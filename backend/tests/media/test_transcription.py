"""Unit tests for the transcription provider abstraction (app/media/transcription).

Mirrors the storage-provider tests: a runtime-checkable ``Transcriber`` Protocol,
a deterministic ``FakeTranscriber`` for CI (ZERO network), a config-driven hosted
default, and a ``get_transcriber()`` dependency that raises 503 when unconfigured
(the STORAGE_BUCKET / SMTP-disabled pattern).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.media.transcription import (
    FakeTranscriber,
    HostedTranscriber,
    Transcriber,
    TranscriptResult,
    get_transcriber,
)


def test_fake_transcriber_satisfies_protocol():
    assert isinstance(FakeTranscriber(), Transcriber)
    assert FakeTranscriber().provider == "fake"


def test_fake_transcriber_is_deterministic():
    fake = FakeTranscriber()
    a = fake.transcribe(b"hello world")
    b = fake.transcribe(b"hello world")
    assert isinstance(a, TranscriptResult)
    assert a.text == b.text
    assert a.duration_s == b.duration_s
    assert a.language == b.language
    # Different input → different (still deterministic) text.
    assert fake.transcribe(b"other").text != a.text


def test_fake_transcriber_honours_fixed_text():
    fake = FakeTranscriber(text="canned transcript", language="fr")
    result = fake.transcribe(b"\x00\x01\x02")
    assert result.text == "canned transcript"
    assert result.language == "fr"


def test_transcript_result_optional_fields_default_none():
    r = TranscriptResult(text="x")
    assert r.duration_s is None
    assert r.language is None


def test_get_transcriber_raises_503_when_unconfigured(monkeypatch):
    from app.media import transcription as mod

    monkeypatch.setattr(mod.settings, "TRANSCRIBE_API_KEY", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        get_transcriber()
    assert exc.value.status_code == 503
    assert "transcription is not configured" in exc.value.detail.lower()


def test_get_transcriber_returns_hosted_when_configured(monkeypatch):
    from app.media import transcription as mod

    monkeypatch.setattr(mod.settings, "TRANSCRIBE_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(mod.settings, "TRANSCRIBE_MODEL", "whisper-1", raising=False)
    t = get_transcriber()
    assert isinstance(t, HostedTranscriber)
    assert isinstance(t, Transcriber)
    assert t.provider == "whisper-1"


def test_hosted_transcriber_never_calls_network_on_construction(monkeypatch):
    """Constructing the hosted provider must not make a network call.

    (The real ASR POST happens only inside ``transcribe`` — never in CI.)
    """
    import app.media.transcription as mod

    def _boom(*a, **k):  # pragma: no cover - guard
        raise AssertionError("no network call may happen at construction time")

    monkeypatch.setattr(mod.httpx, "post", _boom)
    HostedTranscriber(
        api_key="sk-test",
        model="whisper-1",
        endpoint_url="https://example.invalid/v1/audio/transcriptions",
    )
