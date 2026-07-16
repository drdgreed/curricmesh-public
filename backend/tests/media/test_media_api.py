"""Tests for the media upload-url + confirm + list + get + delete API.

Transport pattern mirrors tests/freshness_pipeline/test_watchlist_api.py:
  - asynccontextmanager wrapping httpx.AsyncClient + ASGITransport
  - dependency_overrides for get_db (always) and get_storage (when needed)
  - JWT minted with create_access_token carrying org claim

Test matrix (plan Task 3, Step 2):
  1. upload-url → 201, creates a pending asset, URL contains the storage_key.
  2. confirm after fake.put(storage_key, size) → status=ready, size_bytes set.
  3. confirm with no uploaded object (head returns None) → 400.
  4a. Wrong role (instructor) → 403.
  4b. Storage disabled (no get_storage override, STORAGE_BUCKET empty) → 503.
  5. Cross-org confirm (asset under org A, caller token org B) → 404.

Test matrix (plan Task 4, Step 2):
  6. list returns only the caller-org's assets.
  7. list respects ?status= filter.
  8. list respects ?kind= filter.
  9. GET /{id} on a ready asset includes download_url containing the storage_key.
  10. GET /{id} on a pending asset → download_url is None.
  11. GET /{id} of another org's asset → 404.
  12. DELETE removes the row AND calls storage.delete.
  13. DELETE of another org's asset → 404.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.media.storage import FakeStorageBackend, get_storage
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(
    session: AsyncSession,
    storage: FakeStorageBackend | None = None,
):
    """Yield an AsyncClient wired to the test DB session.

    *storage* — if provided, overrides get_storage so requests use the Fake.
    Omit to let get_storage() run normally (raises 503 when STORAGE_BUCKET
    is empty, which is the test-settings default).
    """

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    if storage is not None:
        app.dependency_overrides[get_storage] = lambda: storage
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    """Mint a Bearer JWT for *role* + *org*."""
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. upload-url creates a pending asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_url_creates_pending_asset(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        resp = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "intro.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "asset_id" in body
    assert "upload_url" in body
    assert "storage_key" in body
    # Presigned URL must embed the storage key so the client knows where it goes.
    assert body["storage_key"] in body["upload_url"]
    # The storage key must be scoped under the org.
    assert str(DEFAULT_ORG_ID) in body["storage_key"]
    # Filename basename is in the key.
    assert "intro.mp4" in body["storage_key"]


# ---------------------------------------------------------------------------
# 2. confirm → ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_marks_asset_ready(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # Step 1: get presigned URL + pending row
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "slide.pdf", "mime": "application/pdf", "kind": "pdf"},
            headers=_auth("program_manager"),
        )
        assert up.status_code == 201
        asset_id = up.json()["asset_id"]
        storage_key = up.json()["storage_key"]

        # Simulate the client PUT (register the object in the fake store)
        fake.put(storage_key, 12345)

        # Step 2: confirm
        conf = await client.post(
            f"/api/v1/media/{asset_id}/confirm",
            json={"checksum": "deadbeef" * 8, "duration_s": None},
            headers=_auth("program_manager"),
        )
    assert conf.status_code == 200, conf.text
    body = conf.json()
    assert body["status"] == "ready"
    assert body["size_bytes"] == 12345
    assert body["checksum"] == "deadbeef" * 8
    assert body["id"] == asset_id


# ---------------------------------------------------------------------------
# 2b. confirm with duration_s (audio file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_sets_duration_s(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "lesson.mp3", "mime": "audio/mpeg", "kind": "audio"},
            headers=_auth("architect"),
        )
        assert up.status_code == 201
        asset_id = up.json()["asset_id"]
        storage_key = up.json()["storage_key"]

        fake.put(storage_key, 4_000_000)

        conf = await client.post(
            f"/api/v1/media/{asset_id}/confirm",
            json={"checksum": "abc123" * 10, "duration_s": 95.5},
            headers=_auth("architect"),
        )
    assert conf.status_code == 200
    assert conf.json()["duration_s"] == pytest.approx(95.5)


# ---------------------------------------------------------------------------
# 3. confirm with no uploaded object → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_without_upload_returns_400(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "ghost.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
        assert up.status_code == 201
        asset_id = up.json()["asset_id"]

        # Intentionally skip fake.put() — object was never uploaded.
        conf = await client.post(
            f"/api/v1/media/{asset_id}/confirm",
            json={"checksum": "doesnotmatter"},
            headers=_auth("architect"),
        )
    assert conf.status_code == 400
    assert "not uploaded" in conf.json()["detail"]


# ---------------------------------------------------------------------------
# 4a. Wrong role → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_role_returns_403(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        resp = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "video.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("instructor"),  # instructor is not in the allowed set
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_instructor_can_read_but_not_write_media(db_session: AsyncSession):
    """Reads (list + get) are open to the author tier so the picker populates for
    instructors who can attach media; writes stay manager-tier (red-team/authoring
    role-gate parity, David 2026-07-06)."""
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # list → 200 (instructor is in the read tier)
        r = await client.get("/api/v1/media", headers=_auth("instructor"))
        assert r.status_code == 200
        # get an unknown id → 404 (passed the read gate, then not found — NOT 403)
        r = await client.get(f"/api/v1/media/{uuid.uuid4()}", headers=_auth("instructor"))
        assert r.status_code == 404
        # delete (a write) → 403 (instructor is NOT in the write tier)
        r = await client.delete(f"/api/v1/media/{uuid.uuid4()}", headers=_auth("instructor"))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 4b. Storage disabled (STORAGE_BUCKET empty in test settings) → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_disabled_returns_503(db_session: AsyncSession, monkeypatch):
    """When STORAGE_BUCKET is empty (default in tests) get_storage() raises 503.

    We do NOT override get_storage here — that's the point.  We DO ensure the
    setting is empty (it already is by default; monkeypatch makes this explicit
    and safe against future env leakage).
    """
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "STORAGE_BUCKET", "")

    # Pass storage=None so _make_transport does NOT add the get_storage override.
    async with _make_transport(db_session, storage=None) as client:
        resp = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "v.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 5. Cross-org confirm → 404
# ---------------------------------------------------------------------------

ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.mark.asyncio
async def test_cross_org_confirm_returns_404(db_session: AsyncSession):
    """Asset created under org A is invisible to a caller with org B's JWT.

    The ORM tenant-scope filter (tenant_scope.py with_loader_criteria) adds
    ``organization_id == current_org`` to every TenantScoped SELECT.  When
    the request carries org B's JWT, tenant_context sets current_org to org B,
    the filter excludes org A's asset, scalar_one_or_none() returns None → 404.
    """
    fake = FakeStorageBackend()

    # Create the asset under DEFAULT_ORG_ID (org A).
    async with _make_transport(db_session, fake) as client:
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "asset.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect", org=DEFAULT_ORG_ID),
        )
        assert up.status_code == 201
        asset_id = up.json()["asset_id"]
        storage_key = up.json()["storage_key"]

    # Simulate the upload so the confirm would otherwise succeed.
    fake.put(storage_key, 99999)

    # Now confirm using org B's JWT — should get 404, not 200.
    async with _make_transport(db_session, fake) as client:
        conf = await client.post(
            f"/api/v1/media/{asset_id}/confirm",
            json={"checksum": "abc"},
            headers=_auth("architect", org=ORG_B),
        )
    assert conf.status_code == 404


# ===========================================================================
# Task 4: list + presigned get + delete
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared helper: create a ready asset in one round-trip
# ---------------------------------------------------------------------------


async def _create_ready_asset(
    client: httpx.AsyncClient,
    fake: FakeStorageBackend,
    filename: str = "test.mp4",
    mime: str = "video/mp4",
    kind: str = "video",
    org: uuid.UUID = DEFAULT_ORG_ID,
    size: int = 1000,
) -> dict:
    """Upload-url + fake.put + confirm → returns the confirmed JSON body."""
    up = await client.post(
        "/api/v1/media/upload-url",
        json={"filename": filename, "mime": mime, "kind": kind},
        headers=_auth("architect", org=org),
    )
    assert up.status_code == 201, up.text
    asset_id = up.json()["asset_id"]
    storage_key = up.json()["storage_key"]
    fake.put(storage_key, size)
    conf = await client.post(
        f"/api/v1/media/{asset_id}/confirm",
        json={"checksum": "deadbeef" * 8},
        headers=_auth("architect", org=org),
    )
    assert conf.status_code == 200, conf.text
    return conf.json()


# ---------------------------------------------------------------------------
# 6. list returns only the caller-org's assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_only_caller_org_assets(db_session: AsyncSession):
    """Assets seeded under DEFAULT_ORG must not be visible to ORG_B.

    The test environment's GUC is pinned to DEFAULT_ORG_ID (session-level
    is_local=false in conftest), so we can only INSERT rows under DEFAULT_ORG.
    Cross-org isolation is verified by listing with ORG_B's JWT: the
    require_roles dependency sets the current_org ContextVar to ORG_B, and the
    ORM with_loader_criteria filter then excludes all DEFAULT_ORG rows.
    """
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # Create two assets under DEFAULT_ORG_ID.
        await _create_ready_asset(client, fake, filename="org_a_1.mp4", org=DEFAULT_ORG_ID)
        await _create_ready_asset(client, fake, filename="org_a_2.mp4", org=DEFAULT_ORG_ID)

        # DEFAULT_ORG caller sees both assets.
        resp_a = await client.get(
            "/api/v1/media", headers=_auth("architect", org=DEFAULT_ORG_ID)
        )
        assert resp_a.status_code == 200, resp_a.text
        assert len(resp_a.json()) == 2

        # ORG_B caller sees nothing — the ORM ContextVar filter excludes all
        # DEFAULT_ORG rows, proving tenant isolation.
        resp_b = await client.get(
            "/api/v1/media", headers=_auth("architect", org=ORG_B)
        )
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json() == []


# ---------------------------------------------------------------------------
# 7. list respects ?status= filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_status(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # Create one pending (no confirm) and one ready asset.
        up_pending = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "pending.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
        assert up_pending.status_code == 201
        pending_id = up_pending.json()["asset_id"]

        ready = await _create_ready_asset(client, fake, filename="ready.mp4")
        ready_id = ready["id"]

        # Filter to status=pending
        r_pending = await client.get(
            "/api/v1/media?status=pending", headers=_auth("architect")
        )
        assert r_pending.status_code == 200
        pending_ids = [a["id"] for a in r_pending.json()]
        assert pending_id in pending_ids
        assert ready_id not in pending_ids

        # Filter to status=ready
        r_ready = await client.get(
            "/api/v1/media?status=ready", headers=_auth("architect")
        )
        assert r_ready.status_code == 200
        ready_ids = [a["id"] for a in r_ready.json()]
        assert ready_id in ready_ids
        assert pending_id not in ready_ids


# ---------------------------------------------------------------------------
# 8. list respects ?kind= filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_kind(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        video = await _create_ready_asset(
            client, fake, filename="lesson.mp4", mime="video/mp4", kind="video"
        )
        pdf = await _create_ready_asset(
            client, fake, filename="slides.pdf", mime="application/pdf", kind="pdf"
        )

        r_video = await client.get(
            "/api/v1/media?kind=video", headers=_auth("architect")
        )
        assert r_video.status_code == 200
        video_ids = [a["id"] for a in r_video.json()]
        assert video["id"] in video_ids
        assert pdf["id"] not in video_ids

        r_pdf = await client.get("/api/v1/media?kind=pdf", headers=_auth("architect"))
        assert r_pdf.status_code == 200
        pdf_ids = [a["id"] for a in r_pdf.json()]
        assert pdf["id"] in pdf_ids
        assert video["id"] not in pdf_ids


# ---------------------------------------------------------------------------
# 9. GET /{id} on a ready asset includes download_url with the storage_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ready_asset_includes_download_url(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        ready = await _create_ready_asset(client, fake, filename="lecture.mp4")
        asset_id = ready["id"]
        storage_key = ready["storage_key"]

        resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == asset_id
    assert body["status"] == "ready"
    assert body["download_url"] is not None
    # Fake presigned_get_url returns a URL embedding the storage_key.
    assert storage_key in body["download_url"]


# ---------------------------------------------------------------------------
# 10. GET /{id} on a pending asset → download_url is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pending_asset_download_url_is_none(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "draft.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
        assert up.status_code == 201
        asset_id = up.json()["asset_id"]

        resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["download_url"] is None


# ---------------------------------------------------------------------------
# 11. GET /{id} of another org's asset → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cross_org_asset_returns_404(db_session: AsyncSession):
    """Asset created under DEFAULT_ORG is invisible when accessed via ORG_B's token.

    Same GUC-vs-ContextVar split as the confirm cross-org test: INSERT uses the
    DB GUC (DEFAULT_ORG), SELECT is filtered at ORM layer via the ContextVar
    (set to ORG_B by require_roles) → scalar_one_or_none() returns None → 404.
    """
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # Create asset under DEFAULT_ORG_ID.
        ready = await _create_ready_asset(
            client, fake, filename="private.mp4", org=DEFAULT_ORG_ID
        )
        asset_id = ready["id"]

        # Try to GET with org B's token.
        resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect", org=ORG_B)
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 12. DELETE removes the row AND calls storage.delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_row_and_calls_storage_delete(db_session: AsyncSession):
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        ready = await _create_ready_asset(client, fake, filename="to_delete.mp4")
        asset_id = ready["id"]
        storage_key = ready["storage_key"]

        # Confirm the key is in the fake store before deletion.
        assert fake.head(storage_key) is not None

        # Delete via API.
        del_resp = await client.delete(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
        assert del_resp.status_code == 204, del_resp.text

        # Storage object is gone.
        assert fake.head(storage_key) is None

        # Row is gone — subsequent GET returns 404.
        get_resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 13. DELETE of another org's asset → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cross_org_asset_returns_404(db_session: AsyncSession):
    """Attempting to DELETE DEFAULT_ORG's asset via ORG_B token → 404.

    The ORM ContextVar filter (ContextVar = ORG_B from require_roles) prevents
    the SELECT from finding the DEFAULT_ORG row, so scalar_one_or_none() → None
    → 404 before any deletion is attempted.
    """
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        # Create asset under DEFAULT_ORG_ID.
        ready = await _create_ready_asset(
            client, fake, filename="other_org.mp4", org=DEFAULT_ORG_ID
        )
        asset_id = ready["id"]

        # Attempt DELETE using org B's token.
        resp = await client.delete(
            f"/api/v1/media/{asset_id}", headers=_auth("architect", org=ORG_B)
        )
    assert resp.status_code == 404
