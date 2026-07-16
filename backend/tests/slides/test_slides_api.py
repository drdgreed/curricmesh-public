"""HTTP tests for the slide-render admin trigger (S1).

Transport mirrors tests/media/test_media_api.py (httpx.AsyncClient + ASGITransport
+ JWT). The render subprocess is mocked; storage is the in-memory Fake. No DB is
needed — the endpoint only touches storage + tenant context.

Matrix:
  1. architect render → 200; keys under decks/<org>/, URLs embed keys, artifacts
     actually land in the Fake (end-to-end render→store→presign via HTTP).
  2. wrong role (instructor) → 403.
  3. storage disabled (STORAGE_BUCKET empty, no override) → 503.
  4. render failure → 502 with a clear message.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from app.auth.jwt import create_access_token
from app.main import app
from app.media.storage import FakeStorageBackend, get_storage
from app.slides import render as render_mod
from tests.conftest import DEFAULT_ORG_ID
from tests.slides.test_render import _FakeRun


@asynccontextmanager
async def _transport(storage: FakeStorageBackend | None = None):
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
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


_DECK = "---\nmarp: true\ntheme: career-forge\n---\n\n# Hi\n"


@pytest.mark.asyncio
async def test_render_endpoint_stores_and_returns_urls(monkeypatch):
    monkeypatch.setattr(render_mod.subprocess, "run", _FakeRun())
    storage = FakeStorageBackend()
    async with _transport(storage) as client:
        resp = await client.post(
            "/api/v1/slides/render",
            json={"deck_md": _DECK},
            headers=_auth("architect"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for fmt in ("pdf", "pptx", "html"):
        key = body[f"{fmt}_key"]
        assert key.startswith(f"decks/{DEFAULT_ORG_ID}/")
        assert key.endswith(f"deck.{fmt}")
        assert key in body[f"{fmt}_url"]
        # Artifact actually reached storage through the HTTP path.
        assert storage.head(key) is not None


@pytest.mark.asyncio
async def test_render_endpoint_wrong_role_forbidden(monkeypatch):
    monkeypatch.setattr(render_mod.subprocess, "run", _FakeRun())
    async with _transport(FakeStorageBackend()) as client:
        resp = await client.post(
            "/api/v1/slides/render",
            json={"deck_md": _DECK},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_render_endpoint_storage_disabled_503():
    # No get_storage override → STORAGE_BUCKET is empty in test settings → 503.
    async with _transport(storage=None) as client:
        resp = await client.post(
            "/api/v1/slides/render",
            json={"deck_md": _DECK},
            headers=_auth("architect"),
        )
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_render_endpoint_render_failure_502(monkeypatch):
    monkeypatch.setattr(render_mod.subprocess, "run", _FakeRun(fail_on="marp-cli"))
    async with _transport(FakeStorageBackend()) as client:
        resp = await client.post(
            "/api/v1/slides/render",
            json={"deck_md": _DECK},
            headers=_auth("architect"),
        )
    assert resp.status_code == 502, resp.text
    assert "render failed" in resp.json()["detail"]
