"""Slice 2 acceptance e2e — attach media via the API, publish, verify the pin.

Exercises the real HTTP surface: create a course + item, attach an owned ready
asset to the item, publish the course, then confirm the released
``ContentVersion`` froze the asset reference (carry-through). The asset is
seeded directly (confirming an upload needs object storage, out of scope here);
everything else goes through the builder API.
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
from app.models.content_model import ContentVersion
from app.models.media import MediaAsset
from tests.conftest import DEFAULT_ORG_ID


@asynccontextmanager
async def _transport(session: AsyncSession):
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


def _auth() -> dict:
    token = create_access_token(
        sub=str(uuid.uuid4()), role="architect", org=DEFAULT_ORG_ID
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_attach_publish_pins_asset(db_session: AsyncSession):
    # Seed a ready owned asset directly (upload/confirm needs storage).
    asset = MediaAsset(
        kind="video",
        filename="keynote.mp4",
        mime="video/mp4",
        storage_key=f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/keynote.mp4",
        status="ready",
    )
    db_session.add(asset)
    await db_session.flush()
    await db_session.commit()
    asset_id = str(asset.id)
    asset_key = asset.storage_key

    async with _transport(db_session) as client:
        # 1. Create a draft course.
        course = await client.post(
            "/api/v1/builder/courses",
            json={"title": "Media E2E"},
            headers=_auth(),
        )
        assert course.status_code == 201, course.text
        course_id = course.json()["id"]

        # 2. Create an item that embeds the asset in its content.
        item = await client.post(
            f"/api/v1/builder/courses/{course_id}/items",
            json={
                "title": "Watch the keynote",
                "content": f"Intro ![[media:{asset_id}]]",
                "week_index": 1,
            },
            headers=_auth(),
        )
        assert item.status_code == 201, item.text
        item_id = item.json()["id"]

        # 3. Attach the asset to the item (explicit link).
        attach = await client.post(
            f"/api/v1/builder/items/{item_id}/media",
            json={"media_asset_id": asset_id},
            headers=_auth(),
        )
        assert attach.status_code == 201, attach.text

        # 4. Publish the course → immutable, active CurriculumVersion.
        pub = await client.post(
            f"/api/v1/builder/courses/{course_id}/publish", headers=_auth()
        )
        assert pub.status_code == 201, pub.text

    # 5. The released ContentVersion pins the exact asset it shipped with.
    cv = (
        await db_session.execute(
            select(ContentVersion).where(ContentVersion.media_refs.isnot(None))
        )
    ).scalar_one()
    assert len(cv.media_refs) == 1
    ref = cv.media_refs[0]
    assert ref["media_asset_id"] == asset_id
    assert ref["storage_key"] == asset_key
    assert ref["filename"] == "keynote.mp4"
