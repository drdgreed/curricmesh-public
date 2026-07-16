"""Integration tests for Task B2 — cascade integration into CCR + dashboard alignment.

Seed strategy:
  curriculum + version → module → three assets (LO, ASSESS, RUBRIC)
  each with an AssetVersion → edges LO→ASSESS, ASSESS→RUBRIC → users

Tests:
  1. submit_ccr with all three affected_asset_ids → cascade populates impact,
     fully_covered=True.
  2. submit_ccr with only LO → WorkflowError (cascaded ASSESS not included).
  3. submit_ccr with LO + ASSESS → succeeds; RUBRIC appears in uncovered.
  4. Dashboard surfaces RUBRIC↔ASSESS misalignment when RUBRIC is stale.
  5. Backward compat: existing A7 structural guard still works when
     affected_asset_ids is NOT provided.
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
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import submit_ccr
from app.core.workflow.rules import WorkflowError
from app.database import get_db
from app.main import app
from tests.conftest import DEFAULT_ORG_ID
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.graph import DependencyEdge
from app.models.structure import Asset, AssetVersion, Module
from app.models.user import User
from app.models.version import Version


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession, role: str = "instructor") -> User:
    user = User(email=f"{uuid.uuid4().hex[:8]}@test.local", role=role)
    session.add(user)
    await session.flush()
    return user


async def _make_curriculum(session: AsyncSession) -> Curriculum:
    cur = Curriculum(
        name=f"Cascade Test {uuid.uuid4().hex[:6]}",
        slug=f"cascade-{uuid.uuid4().hex[:6]}",
    )
    session.add(cur)
    await session.flush()
    return cur


async def _make_version(session: AsyncSession, curriculum_id: uuid.UUID) -> Version:
    v = Version(
        curriculum_id=curriculum_id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    session.add(v)
    await session.flush()
    return v


async def _make_module(session: AsyncSession, version_id: uuid.UUID) -> Module:
    m = Module(version_id=version_id, index=1, focus="cascade test module")
    session.add(m)
    await session.flush()
    return m


async def _make_asset(
    session: AsyncSession,
    module_id: uuid.UUID,
    kind: AssetKind,
    key: str,
) -> Asset:
    a = Asset(kind=kind, key=key, module_id=module_id)
    session.add(a)
    await session.flush()
    return a


async def _make_asset_version(
    session: AsyncSession,
    asset_id: uuid.UUID,
    created_at: datetime | None = None,
) -> AssetVersion:
    av = AssetVersion(
        asset_id=asset_id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    session.add(av)
    await session.flush()
    if created_at is not None:
        # Override the server default with an explicit timestamp for staleness tests.
        await session.execute(
            text(
                "UPDATE asset_versions SET created_at = :ts WHERE id = :id"
            ),
            {"ts": created_at, "id": str(av.id)},
        )
        await session.flush()
    return av


async def _make_edge(
    session: AsyncSession,
    from_asset_id: uuid.UUID,
    to_asset_id: uuid.UUID,
) -> DependencyEdge:
    edge = DependencyEdge(
        from_asset_id=from_asset_id,
        to_asset_id=to_asset_id,
        edge_type="depends_on",
    )
    session.add(edge)
    await session.flush()
    return edge


# ---------------------------------------------------------------------------
# Common fixture: seed the 3-asset graph
# ---------------------------------------------------------------------------


async def _seed_graph(session: AsyncSession):
    """Return (curriculum, version, lo_asset, assess_asset, rubric_asset, author)."""
    author = await _make_user(session)
    cur = await _make_curriculum(session)
    version = await _make_version(session, cur.id)
    module = await _make_module(session, version.id)

    lo = await _make_asset(session, module.id, AssetKind.learning_objectives, "lo_key")
    assess = await _make_asset(session, module.id, AssetKind.assessment, "assess_key")
    rubric = await _make_asset(session, module.id, AssetKind.rubric, "rubric_key")

    # Each asset needs at least one AssetVersion (for alignment checks)
    await _make_asset_version(session, lo.id)
    await _make_asset_version(session, assess.id)
    await _make_asset_version(session, rubric.id)

    # Edges: LO → ASSESS → RUBRIC
    await _make_edge(session, lo.id, assess.id)
    await _make_edge(session, assess.id, rubric.id)

    return cur, version, lo, assess, rubric, author


# ---------------------------------------------------------------------------
# Test 1: cascade computes impact when all assets included
# ---------------------------------------------------------------------------


async def test_submit_ccr_computes_cascade_into_impact(db_session: AsyncSession):
    """submit_ccr with all three asset IDs succeeds; impact.cascade includes
    ASSESS and RUBRIC; fully_covered is True."""
    cur, version, lo, assess, rubric, author = await _seed_graph(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Full cascade test",
        rationale="All assets covered",
        proposed_bump=BumpType.minor,
        affected_kinds={AssetKind.learning_objectives, AssetKind.assessment},
        affected_asset_ids=[lo.id, assess.id, rubric.id],
    )

    assert ccr.impact is not None
    impact = ccr.impact

    # Cascade from LO hits ASSESS and RUBRIC; cascade from ASSESS hits RUBRIC.
    # All dependents are in affected_set, so the cascade list is empty.
    assert impact["cascade"] == []
    assert impact["fully_covered"] is True
    assert impact["uncovered_asset_ids"] == []
    assert set(impact["affected_asset_ids"]) == {
        str(lo.id), str(assess.id), str(rubric.id)
    }


# ---------------------------------------------------------------------------
# Test 2: LO without ASSESS blocked by cascade enforcement
# ---------------------------------------------------------------------------


async def test_lo_change_without_assessment_blocked_by_cascade(db_session: AsyncSession):
    """submit_ccr with only LO in affected_asset_ids raises WorkflowError because
    the cascade reveals a dependent assessment not included."""
    cur, version, lo, assess, rubric, author = await _seed_graph(db_session)

    with pytest.raises(WorkflowError, match=r"(?i)learning.objectives|assessment"):
        await submit_ccr(
            db_session,
            curriculum_id=cur.id,
            author_id=author.id,
            title="LO only — should fail",
            rationale="",
            proposed_bump=BumpType.minor,
            affected_kinds={AssetKind.learning_objectives},
            affected_asset_ids=[lo.id],
        )


# ---------------------------------------------------------------------------
# Test 3: LO + ASSESS allowed; RUBRIC appears as uncovered
# ---------------------------------------------------------------------------


async def test_lo_change_with_assessment_allowed(db_session: AsyncSession):
    """submit_ccr with LO + ASSESS succeeds; RUBRIC (cascaded from ASSESS)
    appears as uncovered since it was not included, but that's not a hard rule."""
    cur, version, lo, assess, rubric, author = await _seed_graph(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="LO + ASSESS covered",
        rationale="Assessment included",
        proposed_bump=BumpType.minor,
        affected_kinds={AssetKind.learning_objectives, AssetKind.assessment},
        affected_asset_ids=[lo.id, assess.id],
    )

    assert ccr.impact is not None
    impact = ccr.impact

    # RUBRIC is a cascaded dependent not in affected_set → uncovered
    assert str(rubric.id) in impact["uncovered_asset_ids"]
    assert impact["fully_covered"] is False

    # RUBRIC should be in the cascade list (cascaded from ASSESS, not in affected_set)
    cascade_ids = {entry["asset_id"] for entry in impact["cascade"]}
    assert str(rubric.id) in cascade_ids

    # ASSESS is in affected_set so it should NOT appear in cascade
    assert str(assess.id) not in cascade_ids


# ---------------------------------------------------------------------------
# Test 4: dashboard surfaces misalignment when RUBRIC is stale
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


async def test_dashboard_surfaces_misalignment(db_session: AsyncSession):
    """When RUBRIC's AssetVersion is older than ASSESS's AssetVersion,
    the dashboard alignment list reports the RUBRIC↔ASSESS misalignment."""
    author = await _make_user(db_session)
    cur = await _make_curriculum(db_session)
    version = await _make_version(db_session, cur.id)

    # Mark this version as active on the curriculum so the dashboard picks it up
    cur.current_version_id = version.id
    db_session.add(cur)
    await db_session.flush()

    module = await _make_module(db_session, version.id)

    assess = await _make_asset(db_session, module.id, AssetKind.assessment, "assess_stale")
    rubric = await _make_asset(db_session, module.id, AssetKind.rubric, "rubric_stale")

    # Stale setup: RUBRIC's AssetVersion created BEFORE ASSESS's AssetVersion
    old_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    new_ts = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    await _make_asset_version(db_session, rubric.id, created_at=old_ts)
    await _make_asset_version(db_session, assess.id, created_at=new_ts)

    # Edge: ASSESS → RUBRIC (RUBRIC depends on ASSESS)
    await _make_edge(db_session, assess.id, rubric.id)

    # Commit so the router's own session sees the data
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth())

    assert resp.status_code == 200
    body = resp.json()

    curr_entry = next(
        (c for c in body["curricula"] if c["id"] == str(cur.id)), None
    )
    assert curr_entry is not None, "Curriculum not found in dashboard response"

    alignment = curr_entry["alignment"]
    assert len(alignment) >= 1, "Expected at least one misalignment"

    mis = next(
        (
            m for m in alignment
            if m["dependent_asset_id"] == str(rubric.id)
            and m["dependency_asset_id"] == str(assess.id)
        ),
        None,
    )
    assert mis is not None, (
        f"Expected RUBRIC({rubric.id})↔ASSESS({assess.id}) misalignment; "
        f"got: {alignment}"
    )


# ---------------------------------------------------------------------------
# Test 5: backward compat — structural guard fires when no asset IDs given
# ---------------------------------------------------------------------------


async def test_backward_compat_structural_guard_still_fires(db_session: AsyncSession):
    """Without affected_asset_ids, the structural assert_lo_change_includes_assessment
    guard still applies — confirms backward compatibility with A7 tests."""
    author = await _make_user(db_session)
    cur = await _make_curriculum(db_session)

    with pytest.raises(WorkflowError, match=r"(?i)assessment|learning.objective"):
        await submit_ccr(
            db_session,
            curriculum_id=cur.id,
            author_id=author.id,
            title="Backward compat guard test",
            rationale="",
            proposed_bump=BumpType.minor,
            affected_kinds={AssetKind.learning_objectives},
            # No affected_asset_ids — uses structural path
        )


async def test_backward_compat_no_impact_when_no_asset_ids(db_session: AsyncSession):
    """Without affected_asset_ids, impact field is None (unchanged behavior)."""
    author = await _make_user(db_session)
    cur = await _make_curriculum(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="No asset IDs — no impact",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds={AssetKind.slides},
        # No affected_asset_ids
    )

    assert ccr.impact is None
