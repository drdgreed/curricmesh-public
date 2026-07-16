"""Integration tests for the CCR/QA/Approval/Release API flow.

Covers:
  - POST /api/v1/ccrs: happy path + RBAC guard
  - GET /api/v1/ccrs: list with optional status filter
  - POST /api/v1/ccrs/{id}/qa: happy path + RBAC guard + WorkflowError→400
  - POST /api/v1/ccrs/{id}/approvals: happy path + RBAC guard
  - POST /api/v1/ccrs/{id}/release: happy path + RBAC guard
  - End-to-end API CCR flow: submit → qa → 2 approvals → release → active version
  - A8: instructor_override role gate, curriculum FK-404, author self-approval
  - B2 API: affected_asset_ids wired through REST — cascade impact populated,
    missing assessment → 400
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from tests.conftest import DEFAULT_ORG_ID
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.graph import DependencyEdge
from app.models.structure import Asset, Module
from app.models.user import User
from app.models.version import Version
from app.core.workflow.rules import QA_DIMENSIONS


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
    cur = Curriculum(name="CCR Test Curriculum", slug=f"ccr-test-{uuid.uuid4().hex[:6]}")
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


_FULL_SCORES = {dim: 4 for dim in QA_DIMENSIONS}


# ---------------------------------------------------------------------------
# POST /api/v1/ccrs
# ---------------------------------------------------------------------------


async def test_create_ccr_instructor(db_session: AsyncSession):
    """instructor role can submit a CCR → 201."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Add new exercises",
                "proposed_bump": "patch",
                "affected_kinds": ["slides"],
            },
            headers=_auth("instructor", sub=str(user.id)),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["title"] == "Add new exercises"


async def test_create_ccr_with_project_kind(db_session: AsyncSession):
    """CCR with affected_kinds=['project'] → 201 (the new AssetKind value).

    `affected_kinds` is CCR metadata only (flows into impact JSONB), never
    written to the native `assetkind` enum column, so no migration is needed.
    """
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Add capstone project",
                "proposed_bump": "minor",
                "affected_kinds": ["project"],
            },
            headers=_auth("instructor", sub=str(user.id)),
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "Add capstone project"


async def test_create_ccr_rbac_forbidden(db_session: AsyncSession):
    """qa_lead cannot submit a CCR → 403."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Forbidden",
                "proposed_bump": "patch",
                "affected_kinds": [],
            },
            headers=_auth("qa_lead"),
        )
    assert resp.status_code == 403


async def test_create_ccr_workflow_error_maps_to_400(db_session: AsyncSession):
    """LO change without assessment → WorkflowError → 400."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "LO only change",
                "proposed_bump": "minor",
                "affected_kinds": ["learning_objectives"],  # missing assessment
            },
            headers=_auth("instructor"),
        )
    assert resp.status_code == 400


async def test_create_ccr_curriculum_not_found_404(db_session: AsyncSession):
    """CCR with nonexistent curriculum_id → 404 (not 500)."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(uuid.uuid4()),
                "title": "Ghost CCR",
                "proposed_bump": "patch",
                "affected_kinds": [],
            },
            headers=_auth("instructor"),
        )
    assert resp.status_code == 404


async def test_instructor_override_forbidden_for_plain_instructor(db_session: AsyncSession):
    """instructor role cannot use instructor_override=True → 403."""
    cur = await _seed_curriculum(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Override attempt",
                "proposed_bump": "patch",
                "affected_kinds": [],
                "instructor_override": True,
            },
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


async def test_instructor_override_allowed_for_instructor_lead(db_session: AsyncSession):
    """instructor_lead CAN use instructor_override=True → 201."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor_lead")
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Override by lead",
                "proposed_bump": "patch",
                "affected_kinds": [],
                "instructor_override": True,
            },
            headers=_auth("instructor_lead", sub=str(user.id)),
        )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/v1/ccrs
# ---------------------------------------------------------------------------


async def test_list_ccrs_any_auth(db_session: AsyncSession):
    """Any authenticated user can list CCRs."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    # Create one CCR first
    async with _make_transport(db_session) as client:
        await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "X", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        resp = await client.get("/api/v1/ccrs", headers=_auth("devops"))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_ccrs_status_filter(db_session: AsyncSession):
    """GET /ccrs?status=draft returns only draft CCRs."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    async with _make_transport(db_session) as client:
        await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Draft CCR", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        resp = await client.get("/api/v1/ccrs?status=draft", headers=_auth("qa_lead"))
    assert resp.status_code == 200
    for item in resp.json():
        assert item["status"] == "draft"


# ---------------------------------------------------------------------------
# POST /api/v1/ccrs/{id}/qa
# ---------------------------------------------------------------------------


async def test_qa_review_pass(db_session: AsyncSession):
    """qa_lead submits a passing QA review → 201."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    reviewer = await _seed_user(db_session, role="qa_lead")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "QA Test", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        assert ccr_resp.status_code == 201
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("qa_lead", sub=str(reviewer.id)),
        )
    assert resp.status_code == 201
    assert resp.json()["verdict"] == "pass"


async def test_qa_review_rbac_forbidden(db_session: AsyncSession):
    """instructor cannot submit QA review → 403."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "RBAC QA", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


async def test_qa_review_invalid_verdict_422(db_session: AsyncSession):
    """Invalid verdict → Pydantic Literal validation → 422 (schema-level rejection).

    Note: With verdict typed as Literal["pass","fail"], Pydantic rejects invalid
    values at the API boundary (422) before the engine ever sees them.
    """
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    reviewer = await _seed_user(db_session, role="qa_lead")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Bad Verdict", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "maybe"},
            headers=_auth("qa_lead", sub=str(reviewer.id)),
        )
    # Pydantic Literal["pass","fail"] rejects invalid values at schema level → 422
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/ccrs/{id}/approvals
# ---------------------------------------------------------------------------


async def test_approval_happy_path(db_session: AsyncSession):
    """program_manager submits an approval → 201."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    approver = await _seed_user(db_session, role="program_manager")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Approval Test", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("program_manager", sub=str(approver.id)),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["decision"] == "approve"
    assert body["role"] == "program_manager"


async def test_approval_rbac_forbidden(db_session: AsyncSession):
    """qa_lead cannot approve → 403."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Forbidden Approval", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("qa_lead"),
        )
    assert resp.status_code == 403


async def test_author_self_approval_returns_400(db_session: AsyncSession):
    """Author approving their own CCR → WorkflowError → 400."""
    cur = await _seed_curriculum(db_session)
    author = await _seed_user(db_session, role="instructor")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Self Approval", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(author.id)),
        )
        assert ccr_resp.status_code == 201
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("instructor", sub=str(author.id)),
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/v1/ccrs/{id}/release
# ---------------------------------------------------------------------------


async def test_release_rbac_forbidden(db_session: AsyncSession):
    """instructor_lead cannot release → 403."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={"curriculum_id": str(cur.id), "title": "Release RBAC", "proposed_bump": "patch", "affected_kinds": []},
            headers=_auth("instructor", sub=str(user.id)),
        )
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("instructor_lead"),
        )
    assert resp.status_code == 403


async def test_release_gate_not_met_400(db_session: AsyncSession):
    """Release with no QA review → WorkflowError (gate not met) → 400."""
    cur = await _seed_curriculum(db_session)
    user = await _seed_user(db_session, role="instructor")
    # Seed an approved version and pass target_version_id in the CCR
    version = await _seed_version(db_session, cur.id, status=LifecycleStatus.approved)

    async with _make_transport(db_session) as client:
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "Premature Release",
                "proposed_bump": "patch",
                "affected_kinds": [],
                "target_version_id": str(version.id),
            },
            headers=_auth("instructor", sub=str(user.id)),
        )
        assert ccr_resp.status_code == 201, ccr_resp.text
        ccr_id = ccr_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager"),
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# End-to-end API CCR flow
# ---------------------------------------------------------------------------


async def test_full_ccr_api_flow(db_session: AsyncSession):
    """Full E2E: POST /ccrs → QA pass → 2 approvals (incl instructor) → release → version active."""
    # Seed users with distinct IDs so approver uniqueness constraint is not hit
    author = await _seed_user(db_session, role="instructor")
    reviewer = await _seed_user(db_session, role="qa_lead")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")
    releaser = await _seed_user(db_session, role="program_manager")

    # Seed curriculum + approved version (pinned as target via target_version_id)
    cur = await _seed_curriculum(db_session)
    version = await _seed_version(db_session, cur.id, status=LifecycleStatus.approved)

    async with _make_transport(db_session) as client:
        # 1. Submit CCR with target_version_id
        ccr_resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "E2E Full Flow",
                "proposed_bump": "minor",
                "affected_kinds": ["slides", "assessment"],
                "target_version_id": str(version.id),
            },
            headers=_auth("instructor", sub=str(author.id)),
        )
        assert ccr_resp.status_code == 201, ccr_resp.text
        ccr_id = ccr_resp.json()["id"]
        assert ccr_resp.json()["status"] == "draft"

        # 2. QA review — passing
        qa_resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("qa_lead", sub=str(reviewer.id)),
        )
        assert qa_resp.status_code == 201, qa_resp.text
        assert qa_resp.json()["verdict"] == "pass"

        # 3a. First approval: program_manager (distinct from author)
        apr1_resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("program_manager", sub=str(pm_approver.id)),
        )
        assert apr1_resp.status_code == 201, apr1_resp.text

        # 3b. Second approval: instructor (satisfies "at least one instructor" gate)
        apr2_resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("instructor", sub=str(instructor_approver.id)),
        )
        assert apr2_resp.status_code == 201, apr2_resp.text

        # 4. Release
        release_resp = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", sub=str(releaser.id)),
        )
        assert release_resp.status_code == 200, release_resp.text
        assert release_resp.json()["status"] == "approved"

    # Verify the version is now active in the DB
    from sqlalchemy import select
    from app.models.version import Version as V
    from app.models.curriculum import Curriculum as C
    result = await db_session.execute(select(V).where(V.id == version.id))
    updated_version = result.scalar_one()
    assert updated_version.status == LifecycleStatus.active

    # Verify curriculum.current_version_id was updated (E)
    cur_result = await db_session.execute(select(C).where(C.id == cur.id))
    updated_cur = cur_result.scalar_one()
    assert updated_cur.current_version_id == version.id


# ---------------------------------------------------------------------------
# B2: affected_asset_ids wired through the REST API
# ---------------------------------------------------------------------------


async def _seed_cascade_graph(session: AsyncSession):
    """Seed curriculum + version + module + LO/ASSESS/RUBRIC assets with edges.

    Returns (cur, version, lo, assess, rubric, author).
    Edge topology: LO → ASSESS → RUBRIC
    """
    from sqlalchemy import select as _select

    author = User(
        email=f"{uuid.uuid4().hex[:8]}@cascade.local",
        role="architect",
        password_hash="x",
    )
    session.add(author)
    await session.flush()

    cur = Curriculum(
        name=f"Cascade API Curriculum {uuid.uuid4().hex[:6]}",
        slug=f"cascade-api-{uuid.uuid4().hex[:6]}",
    )
    session.add(cur)
    await session.flush()

    ver = Version(
        curriculum_id=cur.id,
        major=1, minor=0, patch=0,
        status=LifecycleStatus.draft,
    )
    session.add(ver)
    await session.flush()

    mod = Module(version_id=ver.id, index=1, focus="cascade api module")
    session.add(mod)
    await session.flush()

    lo = Asset(kind=AssetKind.learning_objectives, key="lo_api_key", module_id=mod.id)
    assess = Asset(kind=AssetKind.assessment, key="assess_api_key", module_id=mod.id)
    rubric = Asset(kind=AssetKind.rubric, key="rubric_api_key", module_id=mod.id)
    session.add_all([lo, assess, rubric])
    await session.flush()

    # Edges: LO → ASSESS → RUBRIC
    session.add(DependencyEdge(from_asset_id=lo.id, to_asset_id=assess.id, edge_type="depends_on"))
    session.add(DependencyEdge(from_asset_id=assess.id, to_asset_id=rubric.id, edge_type="depends_on"))
    await session.flush()

    await session.commit()
    return cur, ver, lo, assess, rubric, author


async def test_api_cascade_all_assets_impact_populated(db_session: AsyncSession):
    """POST /api/v1/ccrs with affected_asset_ids=[LO, ASSESS, RUBRIC] → 201
    and the persisted CCR's impact is populated (all dependents are covered).

    B2: verifies affected_asset_ids is wired from the REST layer through to
    submit_ccr and that the impact JSON is stored on the CCR row.
    """
    from sqlalchemy import select as _select
    from app.models.workflow import ChangeRequest

    cur, ver, lo, assess, rubric, author = await _seed_cascade_graph(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "B2 all assets cascade test",
                "proposed_bump": "minor",
                "affected_kinds": ["learning_objectives", "assessment", "rubric"],
                "affected_asset_ids": [str(lo.id), str(assess.id), str(rubric.id)],
            },
            headers=_auth("architect", sub=str(author.id)),
        )

    assert resp.status_code == 201, resp.text
    ccr_id = resp.json()["id"]

    # Fetch the CCR back from DB and verify impact is populated
    result = await db_session.execute(
        _select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(ccr_id))
    )
    ccr = result.scalar_one()
    assert ccr.impact is not None, "impact must be populated when affected_asset_ids is provided"
    impact = ccr.impact
    assert impact["fully_covered"] is True
    assert impact["cascade"] == [], "all dependents are in affected_set — cascade list must be empty"
    assert impact["uncovered_asset_ids"] == []
    assert set(impact["affected_asset_ids"]) == {str(lo.id), str(assess.id), str(rubric.id)}


async def test_api_cascade_lo_without_assessment_returns_400(db_session: AsyncSession):
    """POST /api/v1/ccrs with affected_asset_ids=[LO] (assessment missing)
    → 400 (WorkflowError mapped by the central error handler).

    B2: verifies the cascade enforcement is reachable via the REST API and
    that missing a dependent assessment raises WorkflowError → HTTP 400.
    """
    cur, ver, lo, assess, rubric, author = await _seed_cascade_graph(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/ccrs",
            json={
                "curriculum_id": str(cur.id),
                "title": "B2 LO-only cascade rejection",
                "proposed_bump": "minor",
                "affected_kinds": ["learning_objectives"],
                "affected_asset_ids": [str(lo.id)],  # ASSESS missing → violation
            },
            headers=_auth("architect", sub=str(author.id)),
        )

    assert resp.status_code == 400, resp.text
