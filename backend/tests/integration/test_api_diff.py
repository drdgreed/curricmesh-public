"""Integration tests for GET /api/v1/assets/{asset_id}/diff — Task B4 review findings.

Covers:
  - Happy path: rubric asset with two AssetVersions differing by weight → 200,
    structured.changed reflects the weight change.
  - Cross-asset version IDs → 404.
  - Missing version IDs → 404.
  - Malformed JSON stored in body_ref → 422 (not 500, not 404).
"""

from __future__ import annotations

import json
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


async def _seed_rubric_asset(session: AsyncSession) -> Asset:
    """Create a rubric Asset (no module/project required for diff tests)."""
    asset = Asset(
        kind=AssetKind.rubric,
        key=f"rubric-{uuid.uuid4().hex[:8]}",
    )
    session.add(asset)
    await session.flush()
    return asset


async def _seed_asset_version(
    session: AsyncSession,
    asset: Asset,
    body_ref: str | None,
    major: int = 1,
    minor: int = 0,
    patch: int = 0,
) -> AssetVersion:
    av = AssetVersion(
        asset_id=asset.id,
        major=major,
        minor=minor,
        patch=patch,
        status=LifecycleStatus.draft,
        body_ref=body_ref,
    )
    session.add(av)
    await session.flush()
    return av


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_diff_rubric_weight_change_200(db_session: AsyncSession):
    """Happy path: two rubric versions with a weight change → 200, changed list populated."""
    asset = await _seed_rubric_asset(db_session)

    rubric_a = json.dumps({"criteria": [{"name": "clarity", "weight": 0.2}, {"name": "depth", "weight": 0.8}]})
    rubric_b = json.dumps({"criteria": [{"name": "clarity", "weight": 0.5}, {"name": "depth", "weight": 0.5}]})

    av_from = await _seed_asset_version(db_session, asset, rubric_a, major=1, minor=0, patch=0)
    av_to = await _seed_asset_version(db_session, asset, rubric_b, major=1, minor=1, patch=0)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset.id}/diff",
            params={"from": str(av_from.id), "to": str(av_to.id)},
            headers=_auth(),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "rubric"
    assert body["text"] is None
    assert body["structured"] is not None

    changed = body["structured"]["changed"]
    changed_keys = [c["key"] for c in changed]
    assert "clarity" in changed_keys
    assert "depth" in changed_keys

    clarity_entry = next(c for c in changed if c["key"] == "clarity")
    assert abs(clarity_entry["from"] - 0.2) < 1e-9
    assert abs(clarity_entry["to"] - 0.5) < 1e-9


async def test_diff_cross_asset_version_404(db_session: AsyncSession):
    """Passing a version that belongs to a different asset → 404."""
    asset_a = await _seed_rubric_asset(db_session)
    asset_b = await _seed_rubric_asset(db_session)

    rubric_body = json.dumps({"criteria": [{"name": "clarity", "weight": 0.5}]})

    av_from = await _seed_asset_version(db_session, asset_a, rubric_body, major=1, minor=0, patch=0)
    av_other = await _seed_asset_version(db_session, asset_b, rubric_body, major=1, minor=0, patch=0)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset_a.id}/diff",
            params={"from": str(av_from.id), "to": str(av_other.id)},
            headers=_auth(),
        )

    assert resp.status_code == 404, resp.text


async def test_diff_missing_version_404(db_session: AsyncSession):
    """Passing a non-existent version UUID → 404."""
    asset = await _seed_rubric_asset(db_session)

    rubric_body = json.dumps({"criteria": [{"name": "clarity", "weight": 0.5}]})
    av_from = await _seed_asset_version(db_session, asset, rubric_body, major=1, minor=0, patch=0)
    await db_session.commit()

    missing_id = uuid.uuid4()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset.id}/diff",
            params={"from": str(av_from.id), "to": str(missing_id)},
            headers=_auth(),
        )

    assert resp.status_code == 404, resp.text


async def test_diff_malformed_json_body_422(db_session: AsyncSession):
    """A version whose body_ref is not valid JSON → 422 (not 500, not 404)."""
    asset = await _seed_rubric_asset(db_session)

    good_body = json.dumps({"criteria": [{"name": "clarity", "weight": 0.5}]})
    bad_body = "this is not { valid json !!!"

    av_from = await _seed_asset_version(db_session, asset, good_body, major=1, minor=0, patch=0)
    av_to = await _seed_asset_version(db_session, asset, bad_body, major=1, minor=1, patch=0)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/assets/{asset.id}/diff",
            params={"from": str(av_from.id), "to": str(av_to.id)},
            headers=_auth(),
        )

    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail", "")
    assert "not valid JSON" in detail
