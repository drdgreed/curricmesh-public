"""Integration tests for GET /api/v1/assets/{asset_id}/versions — Task B5.

Covers:
  - Happy path: asset with multiple versions → 200, list ordered newest-first.
  - Asset with no versions → 200, empty list.
  - Non-existent asset_id → 404.
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
from tests.conftest import DEFAULT_ORG_ID
from app.models.enums import AssetKind, LifecycleStatus
from app.models.structure import Asset, AssetVersion


# ---------------------------------------------------------------------------
# Transport helper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str = "instructor") -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_asset(session: AsyncSession) -> Asset:
    asset = Asset(
        kind=AssetKind.rubric,
        key=f"rubric-{uuid.uuid4().hex[:8]}",
    )
    session.add(asset)
    await session.flush()
    return asset


async def _seed_version(
    session: AsyncSession,
    asset: Asset,
    major: int,
    minor: int,
    patch: int,
    status: LifecycleStatus = LifecycleStatus.draft,
) -> AssetVersion:
    av = AssetVersion(
        asset_id=asset.id,
        major=major,
        minor=minor,
        patch=patch,
        status=status,
    )
    session.add(av)
    await session.flush()
    return av


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_versions_ordered_newest_first(db_session: AsyncSession):
    """Three versions → returned newest-first by (major, minor, patch)."""
    asset = await _seed_asset(db_session)
    av1 = await _seed_version(db_session, asset, 1, 0, 0)
    av2 = await _seed_version(db_session, asset, 1, 1, 0)
    av3 = await _seed_version(db_session, asset, 2, 0, 0)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset.id}/versions",
            headers=_auth(),
        )

    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3

    # Newest first
    assert items[0]["semver"] == "2.0.0"
    assert items[0]["id"] == str(av3.id)
    assert items[1]["semver"] == "1.1.0"
    assert items[1]["id"] == str(av2.id)
    assert items[2]["semver"] == "1.0.0"
    assert items[2]["id"] == str(av1.id)

    # Each item has required fields
    for item in items:
        assert "id" in item
        assert "semver" in item
        assert "status" in item
        assert "created_at" in item


async def test_list_versions_empty_asset(db_session: AsyncSession):
    """Asset with no versions → 200 with empty list."""
    asset = await _seed_asset(db_session)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset.id}/versions",
            headers=_auth(),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_list_versions_missing_asset_404(db_session: AsyncSession):
    """Non-existent asset_id → 404."""
    missing_id = uuid.uuid4()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{missing_id}/versions",
            headers=_auth(),
        )

    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()


async def test_list_versions_unauthenticated_401(db_session: AsyncSession):
    """No auth header → 401 (get_current_user rejects the request)."""
    asset = await _seed_asset(db_session)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(f"/api/v1/assets/{asset.id}/versions")

    assert resp.status_code == 401, resp.text
