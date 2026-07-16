"""Integration tests for V3-A analytics — change-velocity & time-in-state.

Covers the engine (app/services/analytics.py) directly against a live DB AND
the thin router (app/routers/analytics.py) through the running ASGI app, plus a
cross-tenant isolation proof (org A's analytics must exclude org B's data).

Seeding strategy
----------------
We seed at the DB level with EXPLICIT ``created_at`` values so dwell-time and
cadence math is deterministic, then drive the documented event_type strings:
  - ``change_requests`` rows for "CCRs opened".
  - ``version_<status>`` history events (target = str(version.id), details
    carry from_status/to_status) — the real transition log produced by
    lifecycle.transition(). Time-in-state and release cadence derive from these.
  - ``version_active`` events mark releases.

The other tenant's rows are written under ``use_org(...)`` so the column default
stamps them with the other org, mirroring test_tenant_api_isolation.py.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent
from app.models.version import Version
from app.models.workflow import ChangeRequest
from app.services import analytics
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID

# A fixed reference instant so bucket boundaries are predictable. 2026-06-01 is a
# Monday → it is itself a week-bucket start.
_T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Transport + token helpers (mirror test_api_dashboard.py)
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


def _auth(role: str, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_curriculum(session: AsyncSession, name: str = "Analytics Curriculum") -> Curriculum:
    cur = Curriculum(name=name, slug=f"anl-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()
    return cur


async def _seed_ccr(
    session: AsyncSession, curriculum_id: uuid.UUID, created_at: datetime
) -> ChangeRequest:
    ccr = ChangeRequest(
        curriculum_id=curriculum_id,
        title="seed ccr",
        status=LifecycleStatus.draft,
        created_at=created_at,
    )
    session.add(ccr)
    await session.flush()
    return ccr


async def _seed_version(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    *,
    created_at: datetime,
    status: LifecycleStatus = LifecycleStatus.active,
    semver: tuple[int, int, int] = (1, 0, 0),
) -> Version:
    v = Version(
        curriculum_id=curriculum_id,
        major=semver[0],
        minor=semver[1],
        patch=semver[2],
        status=status,
        created_at=created_at,
    )
    session.add(v)
    await session.flush()
    return v


async def _seed_transition(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    from_status: LifecycleStatus,
    to_status: LifecycleStatus,
    at: datetime,
) -> HistoryEvent:
    """Emit a real-shaped version_<to> lifecycle event (matches lifecycle.transition())."""
    ev = HistoryEvent(
        actor_id=None,
        event_type=f"version_{to_status.value}",
        target=str(version_id),  # lifecycle.transition() uses the raw UUID
        details={
            "from_status": from_status.value,
            "to_status": to_status.value,
            "actor_role": "architect",
        },
        created_at=at,
    )
    session.add(ev)
    await session.flush()
    return ev


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


async def test_change_velocity_buckets_ccrs_and_releases(db_session: AsyncSession):
    """CCRs opened and version releases land in the correct week buckets."""
    cur = await _seed_curriculum(db_session)
    # Two CCRs in week of _T0, one CCR a week later.
    await _seed_ccr(db_session, cur.id, _T0)
    await _seed_ccr(db_session, cur.id, _T0 + timedelta(days=1))
    await _seed_ccr(db_session, cur.id, _T0 + timedelta(days=7))

    # One version released (went active) in the week of _T0.
    v = await _seed_version(db_session, cur.id, created_at=_T0 - timedelta(days=2))
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
        at=_T0 + timedelta(hours=3),
    )

    buckets = await analytics.change_velocity(db_session, bucket="week")
    by_start = {b.bucket_start.date(): b for b in buckets}

    week0 = by_start[_T0.date()]  # 2026-06-01 Monday
    assert week0.ccrs_opened == 2
    assert week0.versions_released == 1

    week1 = by_start[(_T0 + timedelta(days=7)).date()]
    assert week1.ccrs_opened == 1
    assert week1.versions_released == 0


async def test_time_in_state_reconstructs_dwell_from_transitions(db_session: AsyncSession):
    """Version dwell time is reconstructed from consecutive transitions.

    Timeline for one version:
      created_at = T0           (enters draft)
      +2d  draft   -> review    (dwelt 2 days in draft)
      +5d  review  -> approved  (dwelt 3 days in review)
      +6d  approved-> active    (dwelt 1 day in approved)
      active is the terminal/open state → no completed interval, n must be 0.
    """
    cur = await _seed_curriculum(db_session)
    v = await _seed_version(
        db_session, cur.id, created_at=_T0, status=LifecycleStatus.active
    )
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.draft, to_status=LifecycleStatus.review,
        at=_T0 + timedelta(days=2),
    )
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.review, to_status=LifecycleStatus.approved,
        at=_T0 + timedelta(days=5),
    )
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
        at=_T0 + timedelta(days=6),
    )

    rows = await analytics.time_in_state(db_session)
    by_state = {r.state: r for r in rows}

    # Every lifecycle status is represented.
    assert set(by_state) == set(LifecycleStatus)

    assert by_state[LifecycleStatus.draft].n == 1
    assert by_state[LifecycleStatus.draft].mean_days == 2.0
    assert by_state[LifecycleStatus.review].n == 1
    assert by_state[LifecycleStatus.review].mean_days == 3.0
    assert by_state[LifecycleStatus.approved].n == 1
    assert by_state[LifecycleStatus.approved].mean_days == 1.0

    # active is open (no exit transition) → honest n=0, no fabricated duration.
    assert by_state[LifecycleStatus.active].n == 0
    assert by_state[LifecycleStatus.active].mean_days is None
    # sunset/archived never occurred → honest n=0.
    assert by_state[LifecycleStatus.sunset].n == 0
    assert by_state[LifecycleStatus.archived].n == 0


async def test_release_cadence_days_between(db_session: AsyncSession):
    """Cadence = gaps between consecutive version_active events."""
    cur = await _seed_curriculum(db_session)
    v1 = await _seed_version(db_session, cur.id, created_at=_T0 - timedelta(days=10))
    v2 = await _seed_version(db_session, cur.id, created_at=_T0 - timedelta(days=5))
    v3 = await _seed_version(db_session, cur.id, created_at=_T0 - timedelta(days=1))

    # Releases at T0, T0+10d, T0+30d → gaps of 10 and 20 days.
    for v, day in ((v1, 0), (v2, 10), (v3, 30)):
        await _seed_transition(
            db_session, v.id,
            from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
            at=_T0 + timedelta(days=day),
        )

    cadence = await analytics.release_cadence(db_session)
    assert cadence.releases == 3
    assert cadence.mean_days_between == 15.0  # (10 + 20) / 2
    assert cadence.median_days_between == 15.0


async def test_release_cadence_single_release_no_gap(db_session: AsyncSession):
    """One release → no gap to measure, between-days are None (honest)."""
    cur = await _seed_curriculum(db_session)
    v = await _seed_version(db_session, cur.id, created_at=_T0)
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
        at=_T0,
    )
    cadence = await analytics.release_cadence(db_session)
    assert cadence.releases == 1
    assert cadence.mean_days_between is None
    assert cadence.median_days_between is None


async def test_state_distribution_counts(db_session: AsyncSession):
    """Distribution counts CCRs and versions per current status."""
    cur = await _seed_curriculum(db_session)
    await _seed_ccr(db_session, cur.id, _T0)
    await _seed_ccr(db_session, cur.id, _T0)
    await _seed_version(db_session, cur.id, created_at=_T0, status=LifecycleStatus.active)
    await _seed_version(db_session, cur.id, created_at=_T0, status=LifecycleStatus.draft)

    dist = await analytics.state_distribution(db_session)
    as_set = {(d.entity, d.status, d.count) for d in dist}
    assert ("ccr", LifecycleStatus.draft, 2) in as_set
    assert ("version", LifecycleStatus.active, 1) in as_set
    assert ("version", LifecycleStatus.draft, 1) in as_set


async def test_curriculum_filter_scopes_metrics(db_session: AsyncSession):
    """?curriculum_id confines each aggregate to that curriculum's data."""
    cur_a = await _seed_curriculum(db_session, name="Curriculum A")
    cur_b = await _seed_curriculum(db_session, name="Curriculum B")
    await _seed_ccr(db_session, cur_a.id, _T0)
    await _seed_ccr(db_session, cur_b.id, _T0)

    buckets_a = await analytics.change_velocity(db_session, curriculum_id=cur_a.id)
    total_a = sum(b.ccrs_opened for b in buckets_a)
    assert total_a == 1, "curriculum filter must exclude the other curriculum's CCR"


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------


async def test_overview_endpoint_shape_and_role(db_session: AsyncSession):
    """GET /analytics/overview returns the composed payload for an allowed role."""
    cur = await _seed_curriculum(db_session)
    await _seed_ccr(db_session, cur.id, _T0)

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/analytics/overview", headers=_auth("architect"))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"velocity", "time_in_state", "cadence", "distribution"}
    assert isinstance(body["velocity"], list)
    assert isinstance(body["time_in_state"], list)
    # All six lifecycle states reported in time_in_state (with honest n=0 gaps).
    assert len(body["time_in_state"]) == len(LifecycleStatus)


async def test_analytics_role_gated(db_session: AsyncSession):
    """A role outside {architect, program_manager, qa_lead} is 403'd."""
    async with _make_transport(db_session) as client:
        ok = await client.get("/api/v1/analytics/overview", headers=_auth("qa_lead"))
        forbidden = await client.get(
            "/api/v1/analytics/overview", headers=_auth("instructor")
        )
        unauth = await client.get("/api/v1/analytics/overview")

    assert ok.status_code == 200
    assert forbidden.status_code == 403
    assert unauth.status_code == 401


async def test_velocity_and_cadence_endpoints(db_session: AsyncSession):
    """The /velocity and /cadence endpoints return their schema shapes."""
    cur = await _seed_curriculum(db_session)
    v = await _seed_version(db_session, cur.id, created_at=_T0)
    await _seed_transition(
        db_session, v.id,
        from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
        at=_T0,
    )

    async with _make_transport(db_session) as client:
        vel = await client.get("/api/v1/analytics/velocity", headers=_auth("program_manager"))
        cad = await client.get("/api/v1/analytics/cadence", headers=_auth("program_manager"))

    assert vel.status_code == 200
    assert isinstance(vel.json(), list)
    assert cad.status_code == 200
    assert cad.json()["releases"] == 1


# ---------------------------------------------------------------------------
# Cross-tenant isolation — org A's analytics exclude org B's data
# ---------------------------------------------------------------------------


async def _make_other_org(session: AsyncSession) -> uuid.UUID:
    other = uuid.uuid4()
    await session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other), "n": "Other Analytics Org"},
    )
    return other


async def test_analytics_excludes_other_org_data(db_session: AsyncSession):
    """A DEFAULT_ORG analytics read must not count another org's CCRs/releases.

    Seeds CCRs + a released version in DEFAULT_ORG, and the SAME under a second
    org via use_org(...) (so the organization_id column default stamps them).
    The app-layer tenant auto-filter scopes the engine's reads, so the other
    org's rows are invisible.
    """
    other_org = await _make_other_org(db_session)

    # DEFAULT_ORG data: 1 CCR + 1 release.
    cur_default = await _seed_curriculum(db_session, name="Default Analytics Cur")
    await _seed_ccr(db_session, cur_default.id, _T0)
    v_default = await _seed_version(db_session, cur_default.id, created_at=_T0)
    await _seed_transition(
        db_session, v_default.id,
        from_status=LifecycleStatus.approved, to_status=LifecycleStatus.active,
        at=_T0,
    )

    # OTHER_ORG data: 3 CCRs + 1 release — must NOT appear in DEFAULT's analytics.
    with use_org(other_org):
        cur_other = Curriculum(name="Other Analytics Cur", slug=f"oth-{uuid.uuid4().hex[:8]}")
        db_session.add(cur_other)
        await db_session.flush()
        assert cur_other.organization_id == other_org
        for _ in range(3):
            db_session.add(
                ChangeRequest(
                    curriculum_id=cur_other.id,
                    title="other ccr",
                    status=LifecycleStatus.draft,
                    created_at=_T0,
                )
            )
        v_other = Version(
            curriculum_id=cur_other.id, major=1, minor=0, patch=0,
            status=LifecycleStatus.active, created_at=_T0,
        )
        db_session.add(v_other)
        await db_session.flush()
        db_session.add(
            HistoryEvent(
                actor_id=None,
                event_type="version_active",
                target=str(v_other.id),
                details={"from_status": "approved", "to_status": "active", "actor_role": "architect"},
                created_at=_T0,
            )
        )
        await db_session.flush()

    db_session.expunge_all()

    # Engine reads (scoped to DEFAULT_ORG by the fixture's ContextVar/GUC).
    buckets = await analytics.change_velocity(db_session)
    assert sum(b.ccrs_opened for b in buckets) == 1, "other org's 3 CCRs must be excluded"
    assert sum(b.versions_released for b in buckets) == 1, "other org's release must be excluded"

    cadence = await analytics.release_cadence(db_session)
    assert cadence.releases == 1, "only DEFAULT_ORG's release should be counted"

    dist = await analytics.state_distribution(db_session)
    ccr_rows = [d for d in dist if d.entity == "ccr"]
    # Pin the EXACT count for DEFAULT_ORG's draft CCRs (1), not just membership —
    # a leak would inflate this to 4 (1 default + 3 other) while still being > 0.
    draft_ccr = next(
        (d for d in ccr_rows if d.status == LifecycleStatus.draft), None
    )
    assert draft_ccr is not None, "expected a draft CCR count for DEFAULT_ORG"
    assert draft_ccr.count == 1, (
        "DEFAULT_ORG has exactly 1 draft CCR; a higher count means the other "
        "org's rows leaked into the column-only state_distribution select"
    )
    # And the inflated (org A + org B) total must be absent from every CCR count.
    _COMBINED_DRAFT_CCRS = 1 + 3
    assert all(d.count != _COMBINED_DRAFT_CCRS for d in ccr_rows), (
        "no CCR status count may equal the cross-tenant combined total"
    )

    # And the same through the API.
    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/analytics/overview", headers=_auth("architect"))
    assert resp.status_code == 200
    api_ccrs = sum(b["ccrs_opened"] for b in resp.json()["velocity"])
    assert api_ccrs == 1, "API analytics must not surface another tenant's CCRs"
