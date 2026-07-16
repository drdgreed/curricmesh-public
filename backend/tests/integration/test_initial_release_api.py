"""Slice 5 acceptance — the mandatory QA -> release path through the REAL API.

THE proof for slice 5: an authored draft course, published through the actual
HTTP endpoints, is NOT active until it clears the 6-dimension QA + approval gate:

    POST /builder/courses/{id}/publish   -> 201, candidate (review), NOT active
    POST /ccrs/{ccr}/release  (premature) -> 400 (gate not met), still NOT active
    POST /ccrs/{ccr}/qa        (pass)      -> 201
    POST /ccrs/{ccr}/approvals x2 (incl instructor) -> 201
    GET  /ccrs/{ccr}/gate                  -> can_release: true
    POST /ccrs/{ccr}/release               -> 200, CCR active
    => the CurriculumVersion is now active and active_content_version_id is set.

Transport pattern mirrors tests/integration/test_api_ccr_flow.py.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.builder.models import (
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.core.workflow.rules import QA_DIMENSIONS
from app.database import get_db
from app.main import app
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.user import User
from tests.conftest import DEFAULT_ORG_ID

_FULL_SCORES = {dim: 5 for dim in QA_DIMENSIONS}


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


def _auth(role: str, sub: str) -> dict:
    tok = create_access_token(sub=sub, role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {tok}"}


async def _seed_user(session: AsyncSession, role: str) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local", role=role, password_hash="x"
    )
    session.add(user)
    await session.commit()
    return user


async def _seed_draft(session: AsyncSession) -> uuid.UUID:
    """A minimal valid draft: 1 objective + 1 aligned item."""
    course = DraftCourse(
        organization_id=DEFAULT_ORG_ID, title="Authored Course", status="drafting"
    )
    session.add(course)
    await session.flush()
    obj = DraftObjective(
        organization_id=DEFAULT_ORG_ID,
        draft_course_id=course.id,
        text="Understand the topic",
        week_index=1,
        order_index=0,
    )
    session.add(obj)
    await session.flush()
    item = DraftItem(
        organization_id=DEFAULT_ORG_ID,
        draft_course_id=course.id,
        kind=AssetKind.lesson_plan,
        title="Lesson one",
        content="body",
        week_index=1,
        order_index=0,
    )
    session.add(item)
    await session.flush()
    session.add(
        DraftItemObjective(
            organization_id=DEFAULT_ORG_ID,
            draft_item_id=item.id,
            draft_objective_id=obj.id,
        )
    )
    await session.commit()
    return course.id


async def test_publish_is_not_active_until_qa_and_approvals(db_session: AsyncSession):
    """The full acceptance path: publish -> gate -> release -> active."""
    author = await _seed_user(db_session, role="architect")
    reviewer = await _seed_user(db_session, role="qa_lead")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")
    releaser = await _seed_user(db_session, role="program_manager")

    draft_id = await _seed_draft(db_session)

    async with _make_transport(db_session) as client:
        # 1. Publish -> candidate, NOT active.
        pub = await client.post(
            f"/api/v1/builder/courses/{draft_id}/publish",
            headers=_auth("architect", str(author.id)),
        )
        assert pub.status_code == 201, pub.text
        body = pub.json()
        assert body["active"] is False
        assert body["status"] == "review"
        ccr_id = body["ccr_id"]
        version_id = uuid.UUID(body["version_id"])
        curriculum_id = uuid.UUID(body["curriculum_id"])

        # The curriculum has no active version yet.
        cv = await db_session.get(CurriculumVersion, version_id)
        assert cv.status == LifecycleStatus.review
        curriculum = await db_session.get(Curriculum, curriculum_id)
        assert curriculum.active_content_version_id is None

        # 2. Premature release -> 400 (gate not met); still NOT active.
        premature = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert premature.status_code == 400, premature.text
        await db_session.refresh(curriculum)
        assert curriculum.active_content_version_id is None
        cv = await db_session.get(CurriculumVersion, version_id)
        assert cv.status == LifecycleStatus.review

        # 3. QA pass.
        qa = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("qa_lead", str(reviewer.id)),
        )
        assert qa.status_code == 201, qa.text

        # Still blocked with QA but no approvals yet.
        gate = await client.get(
            f"/api/v1/ccrs/{ccr_id}/gate",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert gate.status_code == 200
        assert gate.json()["can_release"] is False

        # 4. Two approvals, including an instructor.
        for role, sub in (
            ("program_manager", str(pm_approver.id)),
            ("instructor", str(instructor_approver.id)),
        ):
            apr = await client.post(
                f"/api/v1/ccrs/{ccr_id}/approvals",
                json={"decision": "approve"},
                headers=_auth(role, sub),
            )
            assert apr.status_code == 201, apr.text

        # Gate is now satisfied.
        gate = await client.get(
            f"/api/v1/ccrs/{ccr_id}/gate",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert gate.json()["can_release"] is True

        # 5. Release -> activates the candidate.
        rel = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert rel.status_code == 200, rel.text
        assert rel.json()["status"] == "active"

    # The CurriculumVersion is now active + the curriculum points at it.
    cv = await db_session.get(CurriculumVersion, version_id)
    assert cv.status == LifecycleStatus.active
    curriculum = await db_session.get(Curriculum, curriculum_id)
    assert curriculum.active_content_version_id == version_id


async def test_initial_release_ccr_rejects_merge(db_session: AsyncSession):
    """An initial-release CCR has no change_set, so /merge rejects it (400).

    Guards the invariant that the ONLY activation path is the gated /release —
    the fork-replay /merge path cannot be used to bypass QA on a first course.
    """
    author = await _seed_user(db_session, role="architect")
    reviewer = await _seed_user(db_session, role="qa_lead")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")

    draft_id = await _seed_draft(db_session)

    async with _make_transport(db_session) as client:
        pub = await client.post(
            f"/api/v1/builder/courses/{draft_id}/publish",
            headers=_auth("architect", str(author.id)),
        )
        assert pub.status_code == 201, pub.text
        ccr_id = pub.json()["ccr_id"]

        # Even with the full gate satisfied, /merge must not activate it — a first
        # course has no parent to fork against (change_set is None).
        await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("qa_lead", str(reviewer.id)),
        )
        await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("program_manager", str(pm_approver.id)),
        )
        await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth("instructor", str(instructor_approver.id)),
        )

        merge = await client.post(
            f"/api/v1/ccrs/{ccr_id}/merge",
            headers=_auth("program_manager", str(pm_approver.id)),
        )
    assert merge.status_code == 400, merge.text
