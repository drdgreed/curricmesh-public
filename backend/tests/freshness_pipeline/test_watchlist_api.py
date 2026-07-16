"""Tests for the freshness-pipeline watchlist CRUD API.

Uses the same _make_transport / _auth dependency-override transport pattern as
tests/ai_eval/test_gap_research.py. No AI interaction — pure CRUD.

Test sequence (ordered):
  GET empty → POST creates (201 + fields) → GET returns it → PATCH updates fields
  → 404 on unknown id → 403 on wrong role

Also tests seed idempotency at the function level (call upsert_watchlist twice →
4 rows, not 8).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app


# ---------------------------------------------------------------------------
# Transport helpers (mirror tests/ai_eval/test_gap_research.py)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str) -> dict:
    from tests.conftest import DEFAULT_ORG_ID

    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_empty_watchlist(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/freshness/watchlist", headers=_auth("architect"))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_post_creates_watch_item(db_session: AsyncSession):
    payload = {
        "label": "CS294 Agentic AI (F25)",
        "institution": "UC Berkeley",
        "url": "https://rdi.berkeley.edu/agentic-ai/f25",
        "search_hint": "Berkeley CS294 agentic AI syllabus",
    }
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/freshness/watchlist",
            json=payload,
            headers=_auth("architect"),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["label"] == payload["label"]
    assert body["institution"] == payload["institution"]
    assert body["url"] == payload["url"]
    assert body["search_hint"] == payload["search_hint"]
    assert body["active"] is True
    assert "id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_get_returns_created_item(db_session: AsyncSession):
    payload = {
        "label": "CS336 Language Modeling from Scratch",
        "institution": "Stanford",
        "url": "https://cs336.stanford.edu/",
    }
    async with _make_transport(db_session) as client:
        post_resp = await client.post(
            "/api/v1/freshness/watchlist",
            json=payload,
            headers=_auth("program_manager"),
        )
        assert post_resp.status_code == 201

        get_resp = await client.get(
            "/api/v1/freshness/watchlist", headers=_auth("program_manager")
        )
    assert get_resp.status_code == 200
    items = get_resp.json()
    assert len(items) == 1
    assert items[0]["label"] == payload["label"]


@pytest.mark.asyncio
async def test_patch_updates_active_and_url(db_session: AsyncSession):
    payload = {
        "label": "6.8610 Advanced NLP",
        "institution": "MIT",
        "url": "https://mit-6861.github.io/",
    }
    async with _make_transport(db_session) as client:
        post_resp = await client.post(
            "/api/v1/freshness/watchlist",
            json=payload,
            headers=_auth("architect"),
        )
        assert post_resp.status_code == 201
        item_id = post_resp.json()["id"]

        patch_resp = await client.patch(
            f"/api/v1/freshness/watchlist/{item_id}",
            json={"active": False, "url": "https://mit-6861.github.io/syllabus"},
            headers=_auth("architect"),
        )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()
    assert patched["active"] is False
    assert patched["url"] == "https://mit-6861.github.io/syllabus"
    # Unmentioned fields are unchanged.
    assert patched["label"] == payload["label"]
    assert patched["institution"] == payload["institution"]


@pytest.mark.asyncio
async def test_patch_unknown_id_returns_404(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.patch(
            f"/api/v1/freshness/watchlist/{uuid.uuid4()}",
            json={"active": False},
            headers=_auth("architect"),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_role_instructor_forbidden(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/freshness/watchlist", headers=_auth("instructor")
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Seed idempotency test (function-level, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_upsert_idempotent(db_session: AsyncSession):
    """Calling upsert_watchlist twice must produce 4 rows, not 8."""
    from app.models.freshness_pipeline import SourceWatchItem
    from scripts.seed_watchlist import upsert_watchlist
    from tests.conftest import DEFAULT_ORG_ID

    counts1 = await upsert_watchlist(db_session, DEFAULT_ORG_ID)
    # flush already called inside upsert; flush again to ensure rows are visible.
    await db_session.flush()

    counts2 = await upsert_watchlist(db_session, DEFAULT_ORG_ID)
    await db_session.flush()

    assert counts1 == {"created": 4, "skipped": 0}
    assert counts2 == {"created": 0, "skipped": 4}

    all_items = (
        await db_session.execute(select(SourceWatchItem))
    ).scalars().all()
    assert len(all_items) == 4
