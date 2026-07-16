"""Integration tests for GET /api/v1/dashboard."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from tests.conftest import DEFAULT_ORG_ID
from app.models.cohort import Cohort
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent
from app.models.user import User
from app.models.version import Version


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


def _auth(role: str) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(session: AsyncSession, role: str = "architect") -> User:
    user = User(email=f"{uuid.uuid4().hex[:8]}@test.local", role=role, password_hash="x")
    session.add(user)
    await session.commit()
    return user


async def _seed_curriculum(session: AsyncSession, name: str = "Dashboard Curriculum") -> Curriculum:
    cur = Curriculum(name=name, slug=f"dash-{uuid.uuid4().hex[:6]}")
    session.add(cur)
    await session.commit()
    return cur


async def _seed_version(session: AsyncSession, curriculum_id: uuid.UUID, status: LifecycleStatus = LifecycleStatus.draft) -> Version:
    v = Version(curriculum_id=curriculum_id, major=1, minor=0, patch=0, status=status)
    session.add(v)
    await session.commit()
    return v


async def _seed_cohort(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    version_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Cohort:
    c = Cohort(
        curriculum_id=curriculum_id,
        version_id=version_id,
        name="Cohort Alpha",
        start_date=start_date,
        end_date=end_date,
    )
    session.add(c)
    await session.commit()
    return c


async def _seed_history_event(session: AsyncSession, actor_id: uuid.UUID | None = None) -> HistoryEvent:
    event = HistoryEvent(
        actor_id=actor_id,
        event_type="version_review",
        target="version:test",
        details={"note": "seeded for dashboard test"},
    )
    session.add(event)
    await session.commit()
    return event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_dashboard_empty_db(db_session: AsyncSession):
    """Dashboard returns correct shape even with empty DB."""
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth("qa_lead"))
    assert resp.status_code == 200
    body = resp.json()
    assert "curricula" in body
    assert "recent_events" in body
    assert isinstance(body["curricula"], list)
    assert isinstance(body["recent_events"], list)


async def test_dashboard_unauthenticated(db_session: AsyncSession):
    """No token → 401 (get_current_user rejects the request)."""
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard")
    assert resp.status_code == 401


async def test_dashboard_rollup_shape(db_session: AsyncSession):
    """Dashboard includes curricula with versions and cohorts, plus recent history."""
    user = await _seed_user(db_session)
    cur = await _seed_curriculum(db_session, name="Rollup Test")
    v1 = await _seed_version(db_session, cur.id, status=LifecycleStatus.active)
    v2 = await _seed_version(db_session, cur.id, status=LifecycleStatus.draft)
    cohort = await _seed_cohort(db_session, cur.id, v1.id)
    event = await _seed_history_event(db_session, actor_id=user.id)

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth("instructor"))
    assert resp.status_code == 200
    body = resp.json()

    # One curriculum entry
    assert len(body["curricula"]) == 1
    curr_entry = body["curricula"][0]
    assert curr_entry["id"] == str(cur.id)
    assert curr_entry["name"] == "Rollup Test"

    # Two versions
    versions = curr_entry["versions"]
    assert len(versions) == 2
    version_ids = {v["id"] for v in versions}
    assert str(v1.id) in version_ids
    assert str(v2.id) in version_ids

    # Semver strings present
    for v in versions:
        assert "semver" in v
        assert v["semver"] == "1.0.0"

    # One cohort
    assert len(curr_entry["cohorts"]) == 1
    assert curr_entry["cohorts"][0]["id"] == str(cohort.id)
    assert curr_entry["cohorts"][0]["version_id"] == str(v1.id)

    # Recent events
    assert len(body["recent_events"]) >= 1
    event_ids = [e["id"] for e in body["recent_events"]]
    assert str(event.id) in event_ids


async def test_dashboard_recent_events_capped_at_20(db_session: AsyncSession):
    """Dashboard returns at most 20 recent events."""
    # Seed 25 events
    for _ in range(25):
        event = HistoryEvent(
            actor_id=None,
            event_type="ccr_created",
            target="ccr:test",
            details={},
        )
        db_session.add(event)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth("architect"))
    assert resp.status_code == 200
    assert len(resp.json()["recent_events"]) <= 20


async def test_dashboard_multiple_curricula(db_session: AsyncSession):
    """Dashboard lists all curricula."""
    cur1 = await _seed_curriculum(db_session, name="Curriculum Alpha")
    cur2 = await _seed_curriculum(db_session, name="Curriculum Beta")

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth("devops"))
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["curricula"]}
    assert str(cur1.id) in ids
    assert str(cur2.id) in ids


async def test_dashboard_any_role_can_access(db_session: AsyncSession):
    """All roles can access the dashboard."""
    for role in ["instructor", "qa_lead", "devops", "architect", "program_manager", "instructor_lead"]:
        async with _make_transport(db_session) as client:
            resp = await client.get("/api/v1/dashboard", headers=_auth(role))
        assert resp.status_code == 200, f"Failed for role={role}"


async def test_dashboard_cohort_dates_exposed(db_session: AsyncSession):
    """Cohort start_date and end_date are present in the dashboard response."""
    cur = await _seed_curriculum(db_session, name="Dated Cohort Curriculum")
    v = await _seed_version(db_session, cur.id, status=LifecycleStatus.active)
    cohort = await _seed_cohort(
        db_session,
        cur.id,
        v.id,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 6, 30),
    )

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth("architect"))
    assert resp.status_code == 200
    body = resp.json()

    curr_entry = next(c for c in body["curricula"] if c["id"] == str(cur.id))
    assert len(curr_entry["cohorts"]) == 1
    cohort_data = curr_entry["cohorts"][0]
    assert cohort_data["id"] == str(cohort.id)
    assert cohort_data["start_date"] == "2026-01-15"
    assert cohort_data["end_date"] == "2026-06-30"
