"""API tests for draft-item media attach / list / detach (slice 2, task 2).

Transport mirrors ``tests/media/test_media_api.py``: httpx ASGITransport +
``dependency_overrides[get_db]`` + JWTs carrying an org claim. Rows (course /
item / asset) are seeded directly on the DEFAULT_ORG ``db_session`` before the
requests run; cross-org isolation is exercised by calling with an ORG_B token
(the ORM tenant-scope filter then hides the DEFAULT_ORG rows).

Matrix:
  1. attach a ready asset → 201, then list shows it.
  2. list is ordered by order_index.
  3. detach → 204, list is empty.
  4. attach a not-ready (pending) asset → 400.
  5. attach an unknown asset id → 404.
  6. attach against a cross-org item (ORG_B token) → 404.
  7. attach is idempotent (same pair twice → one row, no error).
  8. wrong role → 403.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.builder.models import DraftCourse, DraftItem
from app.database import get_db
from app.main import app
from app.models.enums import AssetKind
from app.models.media import MediaAsset
from tests.conftest import DEFAULT_ORG_ID

ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


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


def _auth(role: str = "architect", org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


async def _seed_item(db: AsyncSession) -> uuid.UUID:
    course = DraftCourse(title="Course")
    db.add(course)
    await db.flush()
    item = DraftItem(
        draft_course_id=course.id, kind=AssetKind.lesson_plan, title="Item"
    )
    db.add(item)
    await db.flush()
    await db.commit()
    return item.id


async def _seed_asset(db: AsyncSession, status: str = "ready") -> uuid.UUID:
    asset = MediaAsset(
        kind="video",
        filename="v.mp4",
        mime="video/mp4",
        storage_key=f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/v.mp4",
        status=status,
    )
    db.add(asset)
    await db.flush()
    await db.commit()
    return asset.id


@pytest.mark.asyncio
async def test_attach_and_list(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id), "order_index": 0},
            headers=_auth(),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["media_asset_id"] == str(asset_id)
        assert resp.json()["status"] == "ready"

        listed = await client.get(
            f"/api/v1/builder/items/{item_id}/media", headers=_auth()
        )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["media_asset_id"] == str(asset_id)
    assert body[0]["filename"] == "v.mp4"


@pytest.mark.asyncio
async def test_list_ordered_by_order_index(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    a1 = await _seed_asset(db_session)
    a2 = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        # Attach a2 with a lower order_index than a1 — list must return a2 first.
        await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(a1), "order_index": 5},
            headers=_auth(),
        )
        await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(a2), "order_index": 1},
            headers=_auth(),
        )
        listed = await client.get(
            f"/api/v1/builder/items/{item_id}/media", headers=_auth()
        )
    ids = [r["media_asset_id"] for r in listed.json()]
    assert ids == [str(a2), str(a1)]


@pytest.mark.asyncio
async def test_detach(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(),
        )
        det = await client.delete(
            f"/api/v1/builder/items/{item_id}/media/{asset_id}", headers=_auth()
        )
        assert det.status_code == 204, det.text
        listed = await client.get(
            f"/api/v1/builder/items/{item_id}/media", headers=_auth()
        )
    assert listed.json() == []


@pytest.mark.asyncio
async def test_detach_unknown_link_404(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    async with _make_transport(db_session) as client:
        det = await client.delete(
            f"/api/v1/builder/items/{item_id}/media/{uuid.uuid4()}",
            headers=_auth(),
        )
    assert det.status_code == 404


@pytest.mark.asyncio
async def test_attach_not_ready_asset_400(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session, status="pending")
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(),
        )
    assert resp.status_code == 400
    assert "not ready" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_attach_unknown_asset_404(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(uuid.uuid4())},
            headers=_auth(),
        )
    assert resp.status_code == 404
    assert "asset" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_attach_cross_org_item_404(db_session: AsyncSession):
    """An item seeded under DEFAULT_ORG is invisible to an ORG_B caller."""
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(org=ORG_B),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_attach_idempotent(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        r1 = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(),
        )
        r2 = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(),
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        listed = await client.get(
            f"/api/v1/builder/items/{item_id}/media", headers=_auth()
        )
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_wrong_role_403(db_session: AsyncSession):
    item_id = await _seed_item(db_session)
    asset_id = await _seed_asset(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": str(asset_id)},
            headers=_auth(role="student"),
        )
    assert resp.status_code == 403
