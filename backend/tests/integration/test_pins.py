"""Integration tests for the version-pinning API (V3-B).

Covers create / list / filter / version-belongs-to-curriculum validation /
unpin, plus a cross-tenant isolation proof: an org-A token cannot see or delete
an org-B pin (404, never a leak). The isolation runs under the test DB superuser
(RLS is bypassed — P-001), so the 404s prove the APPLICATION-layer auto-filter,
exactly as in test_tenant_api_isolation.py.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.curriculum import Curriculum
from app.models.version import Version
from app.models.version_pin import VersionPin
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Transport + token helpers (mirror test_api_curricula.py)
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


def _auth(role: str = "architect", org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_curriculum_and_version(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    cur = Curriculum(name="Pin Curriculum", slug=f"pin-{uuid.uuid4().hex[:6]}")
    session.add(cur)
    await session.flush()
    v = Version(curriculum_id=cur.id, major=2, minor=3, patch=1)
    session.add(v)
    await session.commit()
    return cur.id, v.id


async def _make_other_org(session: AsyncSession) -> uuid.UUID:
    other = uuid.uuid4()
    await session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other), "n": "Other Org"},
    )
    return other


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_pin(db_session: AsyncSession):
    """architect can pin a student; row persists with the request's org."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(v_id),
                "student_label": "Jordan",
                "student_email": "jordan@school.test",
            },
            headers=_auth("architect"),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["student_label"] == "Jordan"
    assert body["version_id"] == str(v_id)
    assert body["status"] == "active"

    # Persisted under the request's org (column default stamps DEFAULT_ORG).
    row = (
        await db_session.execute(
            select(VersionPin).where(VersionPin.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert row.organization_id == DEFAULT_ORG_ID


async def test_create_pin_instructor_allowed(db_session: AsyncSession):
    """instructors enroll students → allowed to pin."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(v_id),
                "student_label": "Sam",
            },
            headers=_auth("instructor"),
        )
    assert resp.status_code == 201


async def test_create_pin_rbac_forbidden(db_session: AsyncSession):
    """A non-privileged role (devops) cannot pin → 403."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(v_id),
                "student_label": "Nope",
            },
            headers=_auth("devops"),
        )
    assert resp.status_code == 403


async def test_create_pin_version_curriculum_mismatch_404(db_session: AsyncSession):
    """Version that doesn't belong to the curriculum → 404."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    other_cur_id, other_v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(other_v_id),  # belongs to other_cur_id
                "student_label": "Mismatch",
            },
            headers=_auth("architect"),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List + filter
# ---------------------------------------------------------------------------


async def test_list_and_filter_pins(db_session: AsyncSession):
    """List returns pins; filter by student_email and status narrows them."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        for label, email, status in [
            ("A", "a@s.test", "active"),
            ("B", "b@s.test", "graduated"),
        ]:
            r = await client.post(
                "/api/v1/pins",
                json={
                    "curriculum_id": str(cur_id),
                    "version_id": str(v_id),
                    "student_label": label,
                    "student_email": email,
                    "status": status,
                },
                headers=_auth("architect"),
            )
            assert r.status_code == 201

        all_pins = await client.get("/api/v1/pins", headers=_auth("instructor"))
        by_email = await client.get(
            "/api/v1/pins", params={"student_email": "a@s.test"}, headers=_auth("instructor")
        )
        by_status = await client.get(
            "/api/v1/pins", params={"status": "graduated"}, headers=_auth("instructor")
        )
        by_curriculum = await client.get(
            f"/api/v1/curricula/{cur_id}/pins", headers=_auth("instructor")
        )

    assert len(all_pins.json()) == 2
    assert [p["student_label"] for p in by_email.json()] == ["A"]
    assert [p["student_label"] for p in by_status.json()] == ["B"]
    assert len(by_curriculum.json()) == 2


async def test_list_pins_pii_role_gated(db_session: AsyncSession):
    """Pins expose student_email (PII) → a non-pin role (devops) is 403'd on reads."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        listing = await client.get("/api/v1/pins", headers=_auth("devops"))
        by_cur = await client.get(
            f"/api/v1/curricula/{cur_id}/pins", headers=_auth("devops")
        )
    assert listing.status_code == 403
    assert by_cur.status_code == 403


# ---------------------------------------------------------------------------
# Unpin
# ---------------------------------------------------------------------------


async def test_unpin(db_session: AsyncSession):
    """DELETE removes the pin (204); a subsequent list is empty."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        created = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(v_id),
                "student_label": "Gone",
            },
            headers=_auth("architect"),
        )
        pin_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/pins/{pin_id}", headers=_auth("program_manager"))
        listing = await client.get("/api/v1/pins", headers=_auth("instructor"))

    assert deleted.status_code == 204
    assert listing.json() == []


async def test_unpin_rbac_forbidden(db_session: AsyncSession):
    """instructor may pin but not unpin → 403."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        created = await client.post(
            "/api/v1/pins",
            json={
                "curriculum_id": str(cur_id),
                "version_id": str(v_id),
                "student_label": "Keep",
            },
            headers=_auth("architect"),
        )
        pin_id = created.json()["id"]
        resp = await client.delete(f"/api/v1/pins/{pin_id}", headers=_auth("instructor"))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tenant isolation: org A cannot see/delete org B's pin (404)
# ---------------------------------------------------------------------------


async def test_pin_tenant_isolation(db_session: AsyncSession):
    """An org-A token cannot list, get-by-curriculum, or delete an org-B pin.

    The other-org pin is created directly under that org's context, then an
    org-A (DEFAULT_ORG) token tries to reach it: list excludes it, the
    curriculum sub-resource is empty for A, and DELETE returns 404 — no leak,
    proving the app-layer auto-filter scopes every access path.
    """
    other_org = await _make_other_org(db_session)

    # Seed a curriculum + version + pin entirely inside the OTHER org's context.
    with use_org(other_org):
        cur = Curriculum(name="B Curriculum", slug=f"b-{uuid.uuid4().hex[:6]}")
        db_session.add(cur)
        await db_session.flush()
        v = Version(curriculum_id=cur.id, major=1, minor=0, patch=0)
        db_session.add(v)
        await db_session.flush()
        pin = VersionPin(
            curriculum_id=cur.id,
            version_id=v.id,
            student_label="Org B Student",
        )
        db_session.add(pin)
        await db_session.commit()
        other_cur_id = cur.id
        other_pin_id = pin.id
        assert pin.organization_id == other_org

    db_session.expunge_all()

    async with _make_transport(db_session) as client:
        # org-A list must NOT include org-B's pin.
        listing = await client.get("/api/v1/pins", headers=_auth(org=DEFAULT_ORG_ID))
        # org-A view of org-B's curriculum pins is empty (curriculum invisible too).
        by_cur = await client.get(
            f"/api/v1/curricula/{other_cur_id}/pins", headers=_auth(org=DEFAULT_ORG_ID)
        )
        # org-A delete of org-B's pin → 404, never a leak.
        deleted = await client.delete(
            f"/api/v1/pins/{other_pin_id}", headers=_auth(org=DEFAULT_ORG_ID)
        )

        # The other org still sees its own pin (proves it wasn't actually deleted).
        other_listing = await client.get("/api/v1/pins", headers=_auth(org=other_org))

    assert all(p["id"] != str(other_pin_id) for p in listing.json())
    assert by_cur.json() == []
    assert deleted.status_code == 404
    assert any(p["id"] == str(other_pin_id) for p in other_listing.json())
