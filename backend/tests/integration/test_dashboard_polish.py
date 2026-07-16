"""Integration tests for the UI-polish dashboard contract additions.

Covers:
  - alignment entries carry friendly names + latest-version timestamps
  - recent_events carry actor_label (user display name / role / system)
  - recent_events carry resolved target_label (version → vX.Y.Z, ccr → title)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from tests.conftest import DEFAULT_ORG_ID
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.graph import DependencyEdge
from app.models.history import HistoryEvent
from app.models.structure import Asset, AssetVersion, Module
from app.models.user import User
from app.models.version import Version
from app.models.workflow import ChangeRequest


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


def _auth(role: str = "architect") -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


async def _set_av_created_at(session: AsyncSession, av_id: uuid.UUID, ts: datetime):
    await session.execute(
        text("UPDATE asset_versions SET created_at = :ts WHERE id = :id"),
        {"ts": ts, "id": str(av_id)},
    )


# ---------------------------------------------------------------------------
# Alignment: friendly names + timestamps
# ---------------------------------------------------------------------------


async def test_alignment_entries_carry_names_and_timestamps(db_session: AsyncSession):
    cur = Curriculum(name="Polish Cur", slug=f"polish-{uuid.uuid4().hex[:6]}")
    db_session.add(cur)
    await db_session.flush()
    version = Version(
        curriculum_id=cur.id, major=1, minor=0, patch=0, status=LifecycleStatus.active
    )
    db_session.add(version)
    await db_session.flush()
    cur.current_version_id = version.id
    db_session.add(cur)
    await db_session.flush()

    module = Module(version_id=version.id, index=4, focus="Sorting")
    db_session.add(module)
    await db_session.flush()

    assess = Asset(kind=AssetKind.assessment, key="assess_key", module_id=module.id)
    rubric = Asset(kind=AssetKind.rubric, key="rubric_key", module_id=module.id)
    db_session.add_all([assess, rubric])
    await db_session.flush()

    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 6, 1, tzinfo=timezone.utc)

    av_assess = AssetVersion(
        asset_id=assess.id, major=1, minor=1, patch=0, status=LifecycleStatus.active
    )
    av_rubric = AssetVersion(
        asset_id=rubric.id, major=1, minor=0, patch=0, status=LifecycleStatus.active
    )
    db_session.add_all([av_assess, av_rubric])
    await db_session.flush()
    await _set_av_created_at(db_session, av_assess.id, new_ts)
    await _set_av_created_at(db_session, av_rubric.id, old_ts)

    # RUBRIC depends on ASSESS; RUBRIC is stale → misalignment.
    db_session.add(
        DependencyEdge(from_asset_id=assess.id, to_asset_id=rubric.id, edge_type="depends_on")
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()

    curr_entry = next(c for c in body["curricula"] if c["id"] == str(cur.id))
    mis = next(
        m for m in curr_entry["alignment"]
        if m["dependent_asset_id"] == str(rubric.id)
    )

    assert mis["dependent_asset_name"] == "Week 4: Sorting · Rubric"
    assert mis["dependency_asset_name"] == "Week 4: Sorting · Assessment"
    # Timestamps reflect each asset's latest AssetVersion.created_at.
    assert mis["dependent_updated_at"].startswith("2026-01-01")
    assert mis["dependency_updated_at"].startswith("2026-06-01")
    # The internal reason field is still present as a fallback.
    assert "reason" in mis


# ---------------------------------------------------------------------------
# History: actor_label + target_label
# ---------------------------------------------------------------------------


async def test_recent_events_actor_label_from_user(db_session: AsyncSession):
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        display_name="Dr. Ada Lovelace",
        role="architect",
        password_hash="x",
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        HistoryEvent(
            actor_id=user.id,
            event_type="ccr_created",
            target=f"ccr:{uuid.uuid4()}",
            details={},
        )
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    body = resp.json()
    evt = next(e for e in body["recent_events"] if e["actor_id"] == str(user.id))
    assert evt["actor_label"] == "Dr. Ada Lovelace"


async def test_recent_events_actor_label_role_and_system(db_session: AsyncSession):
    # Version event with no actor user row → falls back to details.actor_role.
    db_session.add(
        HistoryEvent(
            actor_id=None,
            event_type="version_active",
            target=str(uuid.uuid4()),
            details={"actor_role": "program_manager"},
        )
    )
    # Event with no actor and no role → "System".
    db_session.add(
        HistoryEvent(
            actor_id=None,
            event_type="asset_updated",
            target="not-a-uuid",
            details={},
        )
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    body = resp.json()

    role_evt = next(e for e in body["recent_events"] if e["event_type"] == "version_active")
    assert role_evt["actor_label"] == "program_manager"

    sys_evt = next(e for e in body["recent_events"] if e["event_type"] == "asset_updated")
    assert sys_evt["actor_label"] == "System"
    # Unresolvable target passes through unchanged.
    assert sys_evt["target_label"] == "not-a-uuid"


async def test_recent_events_target_label_version_semver(db_session: AsyncSession):
    cur = Curriculum(name="Ver Cur", slug=f"ver-{uuid.uuid4().hex[:6]}")
    db_session.add(cur)
    await db_session.flush()
    version = Version(
        curriculum_id=cur.id, major=2, minor=3, patch=1, status=LifecycleStatus.active
    )
    db_session.add(version)
    await db_session.flush()

    db_session.add(
        HistoryEvent(
            actor_id=None,
            event_type="version_active",
            target=str(version.id),
            details={"actor_role": "architect"},
        )
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    body = resp.json()
    evt = next(e for e in body["recent_events"] if e["target"] == str(version.id))
    assert evt["target_label"] == "v2.3.1"


async def test_recent_events_target_label_ccr_title(db_session: AsyncSession):
    cur = Curriculum(name="Ccr Cur", slug=f"ccrcur-{uuid.uuid4().hex[:6]}")
    db_session.add(cur)
    await db_session.flush()
    ccr = ChangeRequest(
        curriculum_id=cur.id,
        title="Refresh AI module references",
        proposed_bump="minor",
        status=LifecycleStatus.draft,
    )
    db_session.add(ccr)
    await db_session.flush()

    db_session.add(
        HistoryEvent(
            actor_id=None,
            event_type="ccr_created",
            target=f"ccr:{ccr.id}",
            details={},
        )
    )
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    body = resp.json()
    evt = next(e for e in body["recent_events"] if e["target"] == f"ccr:{ccr.id}")
    assert evt["target_label"] == "Refresh AI module references"


async def test_recent_events_target_label_seed_prefixed_forms(db_session: AsyncSession):
    """The SEED emits 'version:<id>' and 'curriculum:<id>' (prefixed) targets —
    distinct from the runtime engine's bare-UUID / 'ccr:' forms. Both resolve."""
    cur = Curriculum(name="Career Forge", slug=f"cf-{uuid.uuid4().hex[:6]}")
    db_session.add(cur)
    await db_session.flush()
    version = Version(
        curriculum_id=cur.id, major=1, minor=0, patch=0, status=LifecycleStatus.active
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add_all([
        HistoryEvent(
            actor_id=None,
            event_type="version_active",
            target=f"version:{version.id}",   # seed prefixed form
            details={"actor_role": "architect"},
        ),
        HistoryEvent(
            actor_id=None,
            event_type="ccr_created",
            target=f"curriculum:{cur.id}",     # seed prefixed form
            details={},
        ),
    ])
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())
    body = resp.json()
    ver_evt = next(e for e in body["recent_events"] if e["target"] == f"version:{version.id}")
    assert ver_evt["target_label"] == "v1.0.0"
    cur_evt = next(e for e in body["recent_events"] if e["target"] == f"curriculum:{cur.id}")
    assert cur_evt["target_label"] == "Career Forge"
