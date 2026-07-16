"""E2E acceptance test for the media transcription trigger (Phase B, B2).

Against the REAL routers, zero cloud / zero ASR network:

    POST /upload-url          → pending audio asset
    fake.put_bytes(key, data) → simulate the client PUT (real bytes)
    POST /{id}/confirm        → mark ready
    POST /{id}/transcribe     → transcript stored (FakeTranscriber)
    POST /{id}/transcribe     → idempotent (same transcript)

Plus: cross-tenant asset → 404, and 503 when transcription is unconfigured.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.main import app
from app.media.storage import FakeStorageBackend, get_storage
from app.media.transcription import FakeTranscriber, get_transcriber
from tests.media.test_media_api import ORG_B, _auth
from tests.conftest import DEFAULT_ORG_ID


@asynccontextmanager
async def _transport(session: AsyncSession, storage, transcriber=None):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: storage
    if transcriber is not None:
        app.dependency_overrides[get_transcriber] = lambda: transcriber
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _upload_ready_audio(client, storage, *, org=DEFAULT_ORG_ID) -> str:
    """upload-url → put_bytes → confirm; return the ready asset id."""
    up = await client.post(
        "/api/v1/media/upload-url",
        json={"filename": "lecture.m4a", "mime": "audio/mp4", "kind": "audio"},
        headers=_auth("architect", org=org),
    )
    assert up.status_code == 201, up.text
    asset_id = up.json()["asset_id"]
    storage_key = up.json()["storage_key"]
    storage.put_bytes(storage_key, b"the audio payload bytes")
    conf = await client.post(
        f"/api/v1/media/{asset_id}/confirm",
        json={"checksum": "ab" * 32, "duration_s": 42.0},
        headers=_auth("architect", org=org),
    )
    assert conf.status_code == 200, conf.text
    return asset_id


@pytest.mark.asyncio
async def test_transcribe_ready_audio_end_to_end(db_session: AsyncSession):
    storage = FakeStorageBackend()
    transcriber = FakeTranscriber()

    async with _transport(db_session, storage, transcriber) as client:
        asset_id = await _upload_ready_audio(client, storage)

        resp = await client.post(
            f"/api/v1/media/{asset_id}/transcribe", headers=_auth("architect")
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "transcribed"
        assert body["transcript"]["media_asset_id"] == asset_id
        assert body["transcript"]["provider"] == "fake"
        assert body["transcript"]["text"]

        # Idempotent: a second trigger returns the same transcript row.
        again = await client.post(
            f"/api/v1/media/{asset_id}/transcribe", headers=_auth("architect")
        )
        assert again.status_code == 200, again.text
        assert again.json()["transcript"]["id"] == body["transcript"]["id"]


@pytest.mark.asyncio
async def test_transcribe_cross_tenant_returns_404(db_session: AsyncSession):
    """An asset created under org A is invisible to org B → 404, not 200."""
    storage = FakeStorageBackend()
    transcriber = FakeTranscriber()

    async with _transport(db_session, storage, transcriber) as client:
        asset_id = await _upload_ready_audio(client, storage, org=DEFAULT_ORG_ID)

        resp = await client.post(
            f"/api/v1/media/{asset_id}/transcribe",
            headers=_auth("architect", org=ORG_B),
        )
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_transcribe_returns_503_when_unconfigured(db_session: AsyncSession):
    """No get_transcriber override + empty TRANSCRIBE_API_KEY → 503."""
    storage = FakeStorageBackend()

    # transcriber=None → get_transcriber runs for real and 503s (key unset).
    async with _transport(db_session, storage, transcriber=None) as client:
        asset_id = await _upload_ready_audio(client, storage)
        resp = await client.post(
            f"/api/v1/media/{asset_id}/transcribe", headers=_auth("architect")
        )
        assert resp.status_code == 503, resp.text
