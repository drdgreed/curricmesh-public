"""Phase 1 acceptance (slice 6) — the WHOLE authoring loop, end-to-end.

THE Phase-1 proof: a brief becomes an AI-generated draft course (slice 4), which
must clear the mandatory QA -> approval -> release gate (slice 5) before it can
become a live, ACTIVE ``CurriculumVersion``. This composes every Phase-1 seam
through the REAL HTTP endpoints:

    POST /builder/generate-course  (a brief) -> 201, a POPULATED DraftCourse
        (objectives + a lesson + an assessment per objective)
    POST /builder/courses/{id}/publish       -> 201, candidate (review), NOT active
    POST /ccrs/{ccr}/release (premature)      -> 400 (gate not met), still NOT active
    POST /ccrs/{ccr}/qa (pass) + 2 approvals (incl. instructor)
    GET  /ccrs/{ccr}/gate                     -> can_release: true
    POST /ccrs/{ccr}/release                  -> 200, CCR active
    => a now-ACTIVE CurriculumVersion with active_content_version_id set.

The generation step uses a FAKE ``CourseAuthorAI`` injected at the
``get_author_ai`` seam — ZERO real Anthropic calls in CI. Everything after
generation reuses the slice-5 acceptance helpers (auth/seed/transport) verbatim
so this test proves *composition*, not a re-implementation of QA/release.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.models import DraftItem, DraftObjective
from app.database import get_db
from app.main import app
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.routers.authoring_ai import get_author_ai, get_generation_session_scope

# Reuse the slice-5 acceptance helpers verbatim — do NOT reinvent auth/seeding.
from tests.integration.test_initial_release_api import (
    _FULL_SCORES,
    _auth,
    _seed_user,
)

# Reuse the slice-4 fake generator — canned objectives/content/assessment.
from tests.builder.test_course_generator import FakeAuthorAI


@asynccontextmanager
async def _transport(session: AsyncSession, author_ai):
    """Transport with the seams overridden: DB session, AI generator, and the
    background runner's session scope (so the async generate-course flow runs its
    background task against the test session, not a real app-engine connection)."""

    async def _override_get_db():
        yield session

    @asynccontextmanager
    async def _yield_test_session(_org_id):
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_author_ai] = lambda: author_ai
    app.dependency_overrides[get_generation_session_scope] = lambda: _yield_test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _generate_draft(client, headers, brief: dict) -> uuid.UUID:
    """Run the async generate-course flow to completion, returning the draft id.

    POST → 202 + job_id; ASGITransport awaits the background task, so the job is
    already ``complete`` when we poll — return its ``course_id``.
    """
    gen = await client.post(
        "/api/v1/builder/generate-course", json=brief, headers=headers
    )
    assert gen.status_code == 202, gen.text
    job_id = gen.json()["job_id"]
    poll = await client.get(
        f"/api/v1/builder/generate-course/jobs/{job_id}", headers=headers
    )
    assert poll.status_code == 200, poll.text
    body = poll.json()
    assert body["status"] == "complete", body
    assert body["completed_steps"] == body["total_steps"]
    return uuid.UUID(body["course_id"])


def _brief(**overrides) -> dict:
    body = {
        "title": "AI Engineering 101",
        "topic": "Building tool-using agents",
        "learner_profile": {"experience_level": "mid"},
        "target_weeks": 3,
        "objectives_count": 3,
    }
    body.update(overrides)
    return body


async def test_phase1_full_loop_generate_qa_release(db_session: AsyncSession):
    """A brief -> generated draft -> mandatory QA gate -> a live CurriculumVersion.

    The single end-to-end Phase-1 acceptance. Proves an AI-generated course is
    NOT active on publish and can ONLY reach active by clearing QA + approvals.
    """
    author = await _seed_user(db_session, role="architect")
    reviewer = await _seed_user(db_session, role="qa_lead")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")
    releaser = await _seed_user(db_session, role="program_manager")

    async with _transport(db_session, FakeAuthorAI(count=3)) as client:
        # 1. GENERATE (async) — a brief becomes a fully populated, mutable draft.
        draft_id = await _generate_draft(
            client, _auth("architect", str(author.id)), _brief()
        )

        # The draft really is populated in the DB: 3 objectives, 3 lessons, 3 assessments.
        n_obj = (
            await db_session.execute(
                select(func.count())
                .select_from(DraftObjective)
                .where(DraftObjective.draft_course_id == draft_id)
            )
        ).scalar_one()
        assert n_obj == 3
        n_lessons = (
            await db_session.execute(
                select(func.count())
                .select_from(DraftItem)
                .where(
                    DraftItem.draft_course_id == draft_id,
                    DraftItem.kind == AssetKind.lesson_plan,
                )
            )
        ).scalar_one()
        n_assess = (
            await db_session.execute(
                select(func.count())
                .select_from(DraftItem)
                .where(
                    DraftItem.draft_course_id == draft_id,
                    DraftItem.kind == AssetKind.assessment,
                )
            )
        ).scalar_one()
        assert n_lessons == 3
        assert n_assess == 3

        # 2. PUBLISH — a pre-active candidate in review; the course is NOT active.
        pub = await client.post(
            f"/api/v1/builder/courses/{draft_id}/publish",
            headers=_auth("architect", str(author.id)),
        )
        assert pub.status_code == 201, pub.text
        pub_body = pub.json()
        assert pub_body["active"] is False
        assert pub_body["status"] == "review"
        ccr_id = pub_body["ccr_id"]
        version_id = uuid.UUID(pub_body["version_id"])
        curriculum_id = uuid.UUID(pub_body["curriculum_id"])

        cv = await db_session.get(CurriculumVersion, version_id)
        assert cv.status == LifecycleStatus.review
        curriculum = await db_session.get(Curriculum, curriculum_id)
        assert curriculum.active_content_version_id is None

        # 3. PREMATURE RELEASE — an un-QA'd generated course CANNOT reach active.
        premature = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert premature.status_code == 400, premature.text
        await db_session.refresh(curriculum)
        assert curriculum.active_content_version_id is None
        cv = await db_session.get(CurriculumVersion, version_id)
        assert cv.status == LifecycleStatus.review

        # 4. QA PASS.
        qa = await client.post(
            f"/api/v1/ccrs/{ccr_id}/qa",
            json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
            headers=_auth("qa_lead", str(reviewer.id)),
        )
        assert qa.status_code == 201, qa.text

        # QA alone is not enough — approvals are still missing.
        gate = await client.get(
            f"/api/v1/ccrs/{ccr_id}/gate",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert gate.status_code == 200
        assert gate.json()["can_release"] is False

        # 5. TWO APPROVALS, including an instructor.
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

        gate = await client.get(
            f"/api/v1/ccrs/{ccr_id}/gate",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert gate.json()["can_release"] is True

        # 6. RELEASE — the gate is met; the candidate becomes active.
        rel = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert rel.status_code == 200, rel.text
        assert rel.json()["status"] == "active"

    # The generated course is now a LIVE CurriculumVersion — Phase 1 proven end-to-end.
    cv = await db_session.get(CurriculumVersion, version_id)
    assert cv.status == LifecycleStatus.active
    curriculum = await db_session.get(Curriculum, curriculum_id)
    assert curriculum.active_content_version_id == version_id


async def test_generated_course_cannot_reach_active_without_qa(db_session: AsyncSession):
    """Focused invariant: publish a generated course, then try EVERY release with no QA.

    Even with the full set of approvals but NO passing QA review, release stays
    blocked and the course never goes active — the QA dimension of the gate is
    load-bearing, not decorative, on the generated-course path.
    """
    author = await _seed_user(db_session, role="architect")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")
    releaser = await _seed_user(db_session, role="program_manager")

    async with _transport(db_session, FakeAuthorAI(count=2)) as client:
        draft_id = await _generate_draft(
            client,
            _auth("architect", str(author.id)),
            _brief(target_weeks=2, objectives_count=2),
        )

        pub = await client.post(
            f"/api/v1/builder/courses/{draft_id}/publish",
            headers=_auth("architect", str(author.id)),
        )
        assert pub.status_code == 201, pub.text
        ccr_id = pub.json()["ccr_id"]
        version_id = uuid.UUID(pub.json()["version_id"])
        curriculum_id = uuid.UUID(pub.json()["curriculum_id"])

        # Approvals WITHOUT a QA pass.
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

        # Gate must stay closed — no passing QA review.
        gate = await client.get(
            f"/api/v1/ccrs/{ccr_id}/gate",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert gate.json()["can_release"] is False

        rel = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert rel.status_code == 400, rel.text

    cv = await db_session.get(CurriculumVersion, version_id)
    assert cv.status == LifecycleStatus.review
    curriculum = await db_session.get(Curriculum, curriculum_id)
    assert curriculum.active_content_version_id is None
