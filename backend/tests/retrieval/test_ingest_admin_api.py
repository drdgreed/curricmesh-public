"""Admin ingest-trigger endpoint tests (Phase B retrieval infra, Task 3).

Transport pattern mirrors tests/media/test_media_api.py (ASGITransport +
get_db override + JWT). EMBEDDING_PROVIDER defaults to ``fake`` so the endpoint
runs the deterministic offline embedder — NO real embedding API is called.

  1. POST ingest → 201, writes chunks (idempotent on a second call).
  2. Wrong role (instructor) → 403.
  3. Unknown version id → 404.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.retrieval import ContentChunk
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import seed_version_with_members


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


def _auth(role: str, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_ingest_trigger_writes_chunks_idempotently(db_session: AsyncSession):
    version = await seed_version_with_members(
        db_session, texts=["alpha beta gamma", "delta epsilon"]
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        r1 = await client.post(
            f"/api/v1/admin/retrieval/versions/{version.id}/ingest",
            headers=_auth("architect"),
        )
        assert r1.status_code == 201, r1.text
        n1 = r1.json()["chunks_written"]
        assert n1 > 0

        r2 = await client.post(
            f"/api/v1/admin/retrieval/versions/{version.id}/ingest",
            headers=_auth("architect"),
        )
        assert r2.status_code == 201
        assert r2.json()["chunks_written"] == n1

    total = (
        await db_session.execute(
            select(func.count())
            .select_from(ContentChunk)
            .where(ContentChunk.curriculum_version_id == version.id)
        )
    ).scalar_one()
    assert total == n1, "second ingest must not duplicate chunks"


@pytest.mark.asyncio
async def test_ingest_trigger_forbidden_for_wrong_role(db_session: AsyncSession):
    version = await seed_version_with_members(db_session, texts=["x y z"])
    await db_session.commit()
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/admin/retrieval/versions/{version.id}/ingest",
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_trigger_unknown_version_404(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/admin/retrieval/versions/{uuid.uuid4()}/ingest",
            headers=_auth("architect"),
        )
    assert resp.status_code == 404
