"""E2E acceptance test for the media subsystem.

Full own-the-media round-trip against the REAL router, zero cloud:

    POST /upload-url
    → fake.put(storage_key, size)          # simulate client PUT
    → POST /{id}/confirm                    # server verifies HEAD, marks ready
    → GET  /{id}                           # status=ready, download_url present
    → GET  /media                          # asset visible in list
    → DELETE /{id}                         # 204; row gone + storage object gone

Transport / auth helpers are imported from test_media_api (reuse without
duplication).

Instance-sharing mechanism
--------------------------
``_make_transport(session, storage)`` installs::

    app.dependency_overrides[get_storage] = lambda: storage

The lambda is a closure over *storage* — every request inside the
``async with`` block resolves ``get_storage()`` to the **same**
``FakeStorageBackend`` instance.  ``fake.put(key, size)`` registered before
``/confirm`` is therefore visible to ``storage.head(key)`` inside the confirm
handler, satisfying the upload-verification step.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.media.storage import FakeStorageBackend
from tests.media.test_media_api import _auth, _make_transport


@pytest.mark.asyncio
async def test_media_full_round_trip(db_session: AsyncSession) -> None:
    """Acceptance: upload-url → simulate PUT → confirm → get → list → delete.

    All six steps run inside one _make_transport context so they share the same
    DB session and the same FakeStorageBackend instance.
    """
    fake = FakeStorageBackend()
    upload_size = 8_388_608  # 8 MiB

    async with _make_transport(db_session, fake) as client:
        # ── Step 1: request a presigned upload URL ────────────────────────────
        up = await client.post(
            "/api/v1/media/upload-url",
            json={"filename": "e2e_lecture.mp4", "mime": "video/mp4", "kind": "video"},
            headers=_auth("architect"),
        )
        assert up.status_code == 201, up.text
        asset_id = up.json()["asset_id"]
        upload_url = up.json()["upload_url"]
        storage_key = up.json()["storage_key"]

        # The presigned URL must embed the storage key.
        assert storage_key in upload_url

        # ── Step 2: simulate client PUT ───────────────────────────────────────
        # The dependency override `lambda: fake` is a closure; calling put() on
        # this instance makes head() return the size when /confirm calls it.
        fake.put(storage_key, upload_size)

        # ── Step 3: confirm — server HEAD-checks storage, marks asset ready ──
        conf = await client.post(
            f"/api/v1/media/{asset_id}/confirm",
            json={"checksum": "abcdef01" * 8, "duration_s": 120.0},
            headers=_auth("architect"),
        )
        assert conf.status_code == 200, conf.text
        confirmed = conf.json()
        assert confirmed["status"] == "ready"
        assert confirmed["size_bytes"] == upload_size
        assert confirmed["checksum"] == "abcdef01" * 8

        # ── Step 4: GET the asset — ready + download_url present ─────────────
        get_resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
        assert get_resp.status_code == 200, get_resp.text
        detail = get_resp.json()
        assert detail["status"] == "ready"
        assert detail["download_url"] is not None
        # FakeStorageBackend.presigned_get_url returns f"https://fake-storage/get/{key}"
        assert storage_key in detail["download_url"]

        # ── Step 5: LIST — the asset is visible ──────────────────────────────
        list_resp = await client.get("/api/v1/media", headers=_auth("architect"))
        assert list_resp.status_code == 200, list_resp.text
        ids_in_list = [a["id"] for a in list_resp.json()]
        assert asset_id in ids_in_list

        # ── Step 6: DELETE — 204; row gone; storage object gone ──────────────
        del_resp = await client.delete(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
        assert del_resp.status_code == 204, del_resp.text

        # Row is gone — subsequent GET returns 404.
        gone_resp = await client.get(
            f"/api/v1/media/{asset_id}", headers=_auth("architect")
        )
        assert gone_resp.status_code == 404

        # Storage object is also gone — delete_media called storage.delete().
        assert fake.head(storage_key) is None
