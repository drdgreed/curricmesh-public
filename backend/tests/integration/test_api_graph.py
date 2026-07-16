"""Integration tests for Task B3 — GET /api/v1/curricula/{id}/graph.

Seed strategy:
  curriculum + active version → module → three assets (LO, ASSESS, RUBRIC)
  edges: LO → ASSESS → RUBRIC
  asset versions: LO and ASSESS at a recent timestamp; RUBRIC at an older
  timestamp so it is detected as misaligned.

Tests:
  1. Happy path: 3 nodes, 2 edges, RUBRIC in misaligned_asset_ids.
  2. 404 when curriculum_id does not exist.
  3. Empty graph when curriculum has no active version.
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
from app.models.structure import Asset, AssetVersion, Module
from app.models.version import Version


# ---------------------------------------------------------------------------
# Transport helper (same pattern used across integration tests)
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


async def _make_curriculum_with_active_version(
    session: AsyncSession,
) -> tuple[Curriculum, Version]:
    """Seed a curriculum with an active version set as current_version_id."""
    cur = Curriculum(
        name=f"Graph Test {uuid.uuid4().hex[:6]}",
        slug=f"graph-{uuid.uuid4().hex[:6]}",
    )
    session.add(cur)
    await session.flush()

    version = Version(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    session.add(version)
    await session.flush()

    # Point curriculum at this version
    cur.current_version_id = version.id
    session.add(cur)
    await session.flush()

    return cur, version


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
    major: int = 1,
    minor: int = 0,
    patch: int = 0,
    created_at: datetime | None = None,
) -> AssetVersion:
    av = AssetVersion(
        asset_id=asset_id,
        major=major,
        minor=minor,
        patch=patch,
        status=LifecycleStatus.active,
    )
    session.add(av)
    await session.flush()
    if created_at is not None:
        await session.execute(
            text("UPDATE asset_versions SET created_at = :ts WHERE id = :id"),
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
# Test 1: happy path — 3 nodes, 2 edges, stale RUBRIC in misaligned_asset_ids
# ---------------------------------------------------------------------------


async def test_graph_happy_path(db_session: AsyncSession):
    """GET /graph returns 3 nodes, 2 edges, and RUBRIC in misaligned_asset_ids
    because its AssetVersion predates ASSESS's AssetVersion."""

    cur, version = await _make_curriculum_with_active_version(db_session)

    module = Module(version_id=version.id, index=1, focus="test module")
    db_session.add(module)
    await db_session.flush()

    lo = await _make_asset(db_session, module.id, AssetKind.learning_objectives, "lo_key")
    assess = await _make_asset(db_session, module.id, AssetKind.assessment, "assess_key")
    rubric = await _make_asset(db_session, module.id, AssetKind.rubric, "rubric_key")

    old_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    new_ts = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    # LO and ASSESS are fresh; RUBRIC is stale (predates ASSESS)
    await _make_asset_version(db_session, lo.id, created_at=new_ts)
    await _make_asset_version(db_session, assess.id, major=1, minor=1, patch=0, created_at=new_ts)
    await _make_asset_version(db_session, rubric.id, created_at=old_ts)

    # Edges: LO → ASSESS → RUBRIC (ASSESS depends on LO; RUBRIC depends on ASSESS)
    await _make_edge(db_session, lo.id, assess.id)
    await _make_edge(db_session, assess.id, rubric.id)

    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/curricula/{cur.id}/graph",
            headers=_auth(),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 3 nodes
    assert len(body["nodes"]) == 3, f"Expected 3 nodes, got {len(body['nodes'])}: {body['nodes']}"

    # 2 edges
    assert len(body["edges"]) == 2, f"Expected 2 edges, got {len(body['edges'])}: {body['edges']}"

    # RUBRIC is misaligned (stale relative to ASSESS); LO and ASSESS are not
    misaligned = body["misaligned_asset_ids"]
    assert str(rubric.id) in misaligned, (
        f"Expected RUBRIC({rubric.id}) in misaligned_asset_ids; got: {misaligned}"
    )
    assert str(lo.id) not in misaligned, (
        f"Expected LO({lo.id}) NOT in misaligned_asset_ids; got: {misaligned}"
    )
    assert str(assess.id) not in misaligned, (
        f"Expected ASSESS({assess.id}) NOT in misaligned_asset_ids; got: {misaligned}"
    )

    # Node shapes
    node_ids = {n["id"] for n in body["nodes"]}
    assert str(lo.id) in node_ids
    assert str(assess.id) in node_ids
    assert str(rubric.id) in node_ids

    # Spot-check ASSESS node has latest_version "1.1.0"
    assess_node = next(n for n in body["nodes"] if n["id"] == str(assess.id))
    assert assess_node["latest_version"] == "1.1.0"
    assert assess_node["kind"] == "assessment"
    # label is now the friendly name (container · kind), not the raw asset.key
    assert assess_node["label"] == "Week 1: test module · Assessment"


# ---------------------------------------------------------------------------
# Test 2: 404 for nonexistent curriculum
# ---------------------------------------------------------------------------


async def test_graph_404_for_unknown_curriculum(db_session: AsyncSession):
    """Requesting graph for a nonexistent curriculum_id returns 404."""
    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/curricula/{uuid.uuid4()}/graph",
            headers=_auth(),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: empty graph when no active version
# ---------------------------------------------------------------------------


async def test_graph_empty_when_no_active_version(db_session: AsyncSession):
    """A curriculum with no current_version_id returns empty nodes/edges."""
    cur = Curriculum(
        name=f"Empty Graph {uuid.uuid4().hex[:6]}",
        slug=f"empty-graph-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(cur)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.get(
            f"/api/v1/curricula/{cur.id}/graph",
            headers=_auth(),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["misaligned_asset_ids"] == []
