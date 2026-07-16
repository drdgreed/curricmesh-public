"""Integration tests for curricula and versions API routers.

Tests:
  - POST /api/v1/curricula: happy path + RBAC guard
  - GET /api/v1/curricula: list (any auth)
  - GET /api/v1/curricula/{id}: get by id
  - POST /api/v1/curricula/{id}/versions: happy path + RBAC guard
  - GET /api/v1/curricula/{id}/versions: list
  - POST /api/v1/versions/{id}/transition: happy path + PermissionDenied (403) + IllegalTransition (409)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from tests.conftest import DEFAULT_ORG_ID
from app.main import app
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.user import User
from app.models.version import Version


# ---------------------------------------------------------------------------
# Shared transport helper (mirrors test_login.py pattern)
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


def _tok(role: str, sub: str | None = None) -> str:
    return create_access_token(sub=sub or str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)


def _auth(role: str, sub: str | None = None) -> dict:
    return {"Authorization": f"Bearer {_tok(role, sub)}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(session: AsyncSession, role: str = "architect") -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        role=role,
        password_hash="x",
    )
    session.add(user)
    await session.commit()
    return user


async def _seed_curriculum(session: AsyncSession) -> Curriculum:
    cur = Curriculum(name="Test Curriculum", slug=f"test-{uuid.uuid4().hex[:6]}")
    session.add(cur)
    await session.commit()
    return cur


async def _seed_version(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    status: LifecycleStatus = LifecycleStatus.draft,
) -> Version:
    v = Version(
        curriculum_id=curriculum_id,
        major=1,
        minor=0,
        patch=0,
        status=status,
    )
    session.add(v)
    await session.commit()
    return v


# ---------------------------------------------------------------------------
# POST /api/v1/curricula
# ---------------------------------------------------------------------------


async def test_create_curriculum_architect(db_session: AsyncSession):
    """Architect role → 201 + curriculum object returned."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/curricula",
            json={"name": "Intro to Python", "slug": "intro-python"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Intro to Python"
    assert body["slug"] == "intro-python"
    assert "id" in body


async def test_create_curriculum_program_manager(db_session: AsyncSession):
    """program_manager is also allowed."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/curricula",
            json={"name": "Data Science", "slug": "data-science"},
            headers=_auth("program_manager"),
        )
    assert resp.status_code == 201


async def test_create_curriculum_rbac_forbidden(db_session: AsyncSession):
    """instructor role → 403."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/curricula",
            json={"name": "Hack", "slug": "hack"},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


async def test_create_curriculum_unauthenticated(db_session: AsyncSession):
    """No token → 401 (get_current_user rejects the request)."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/curricula",
            json={"name": "Hack", "slug": "hack"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/curricula
# ---------------------------------------------------------------------------


async def test_list_curricula_any_auth(db_session: AsyncSession):
    """Any authenticated user can list curricula."""
    await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/curricula", headers=_auth("qa_lead"))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


# ---------------------------------------------------------------------------
# GET /api/v1/curricula/{id}
# ---------------------------------------------------------------------------


async def test_get_curriculum_by_id(db_session: AsyncSession):
    """Get specific curriculum — any auth."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.get(f"/api/v1/curricula/{cur.id}", headers=_auth("instructor"))
    assert resp.status_code == 200
    assert resp.json()["id"] == str(cur.id)


async def test_get_curriculum_not_found(db_session: AsyncSession):
    """Unknown ID → 404."""
    async with _make_transport(db_session) as client:
        resp = await client.get(f"/api/v1/curricula/{uuid.uuid4()}", headers=_auth("instructor"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/curricula/{id}/versions
# ---------------------------------------------------------------------------


async def test_create_version_architect(db_session: AsyncSession):
    """architect → 201 + version object."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur.id}/versions",
            json={"major": 1, "minor": 0, "patch": 0},
            headers=_auth("architect"),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["major"] == 1
    assert body["status"] == "draft"


async def test_create_version_rbac_forbidden(db_session: AsyncSession):
    """instructor → 403 (only architect allowed for version creation)."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur.id}/versions",
            json={"major": 1, "minor": 0, "patch": 0},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


async def test_create_version_curriculum_not_found(db_session: AsyncSession):
    """Creating a version for a nonexistent curriculum → 404."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{uuid.uuid4()}/versions",
            json={"major": 1, "minor": 0, "patch": 0},
            headers=_auth("architect"),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/curricula/{id}/versions
# ---------------------------------------------------------------------------


async def test_list_versions(db_session: AsyncSession):
    """List versions for a curriculum — any auth."""
    cur = await _seed_curriculum(db_session)
    await _seed_version(db_session, cur.id)
    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/curricula/{cur.id}/versions",
            headers=_auth("devops"),
        )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ---------------------------------------------------------------------------
# POST /api/v1/versions/{id}/transition
# ---------------------------------------------------------------------------


async def test_transition_draft_to_review(db_session: AsyncSession):
    """instructor can transition draft → review."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    version = await _seed_version(db_session, cur.id, status=LifecycleStatus.draft)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/versions/{version.id}/transition",
            json={"to_status": "review"},
            headers=_auth("instructor", sub=str(user.id)),
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "review"


async def test_transition_permission_denied_maps_to_403(db_session: AsyncSession):
    """devops cannot move draft → review → PermissionDenied → 403."""
    cur = await _seed_curriculum(db_session)
    version = await _seed_version(db_session, cur.id, status=LifecycleStatus.draft)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/versions/{version.id}/transition",
            json={"to_status": "review"},
            headers=_auth("devops"),
        )
    assert resp.status_code == 403


async def test_transition_illegal_transition_maps_to_409(db_session: AsyncSession):
    """draft → active is not a legal edge → IllegalTransition → 409."""
    cur = await _seed_curriculum(db_session)
    version = await _seed_version(db_session, cur.id, status=LifecycleStatus.draft)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/versions/{version.id}/transition",
            json={"to_status": "active"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 409


async def test_transition_version_not_found(db_session: AsyncSession):
    """Unknown version ID → 404."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/versions/{uuid.uuid4()}/transition",
            json={"to_status": "review"},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 404
