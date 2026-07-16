"""Async POST /api/v1/builder/generate-course + GET .../jobs/{job_id} (transport).

Drives the async-rework of the full-course orchestrator through HTTP (httpx
ASGITransport) against the RLS-enabled ``db_session`` fixture. The heavy work is
scheduled on a FastAPI ``BackgroundTask``; ASGITransport awaits background tasks
before the response resolves, so after ``await client.post(...)`` the job has
already run to completion and we can poll it.

Injection (ZERO real Anthropic calls in CI):
  * ``get_author_ai`` → a FAKE ``CourseAuthorAI`` (canned objectives/items).
  * ``get_generation_session_scope`` → a factory yielding the test ``db_session``
    (already org-scoped to DEFAULT_ORG), so the background runner does NOT open a
    real connection on the app engine, yet still sets/uses the org context.

Asserted:
  * happy path — POST → 202 + job_id → poll → complete, course_id points at a
    real, populated DraftCourse; the fake AI was used (no real calls).
  * a generator that raises → job ``failed`` with an error, and NO partial course
    is leaked.
  * ``objectives_count > 20`` → 422 at body validation, before any job is created.
  * a non-author role → 403 (role gate).
  * no API key → 503 (``get_author_ai`` guard, not overridden).
  * cross-tenant job fetch → 404; another user's job (same tenant) → 404.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest import mock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.builder.models import DraftCourse, DraftItem, DraftObjective
from app.config import settings
from app.database import get_db
from app.main import app
from app.routers.authoring_ai import get_author_ai, get_generation_session_scope
from tests.builder.test_course_generator import FakeAuthorAI


class FailingObjectivesAuthorAI(FakeAuthorAI):
    """Its very first call (objectives) raises — nothing gets assembled."""

    async def generate_objectives(
        self, *, topic, learner_profile, count=5, language="en"
    ):
        raise RuntimeError("model refused the objectives")


def _auth(role: str, *, sub: str | None = None, org=None) -> dict:
    from tests.conftest import DEFAULT_ORG_ID

    token = create_access_token(
        sub=sub or str(uuid.uuid4()), role=role, org=org or DEFAULT_ORG_ID
    )
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _transport(session: AsyncSession, author_ai=None, *, scope_session=True):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    if author_ai is not None:
        app.dependency_overrides[get_author_ai] = lambda: author_ai
    if scope_session:
        # The background runner opens its own org-scoped session in prod; in
        # tests we hand it the already-org-scoped test session so it makes no
        # real app-engine connection but still runs the full runner path.
        @asynccontextmanager
        async def _yield_test_session(_org_id):
            yield session

        app.dependency_overrides[get_generation_session_scope] = (
            lambda: _yield_test_session
        )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _brief(**overrides) -> dict:
    body = {
        "title": "AI Engineering 101",
        "topic": "Building tool-using agents",
        "learner_profile": {"experience_level": "mid"},
        "target_weeks": 4,
        "objectives_count": 4,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_generate_course_async_completes_with_course(db_session: AsyncSession):
    fake = FakeAuthorAI(count=4)
    async with _transport(db_session, fake) as client:
        headers = _auth("instructor")
        resp = await client.post(
            "/api/v1/builder/generate-course", json=_brief(), headers=headers
        )
        # 202 + job_id — the work is scheduled, not done inline.
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        assert job_id

        # ASGITransport awaited the background task; the job is complete now.
        poll = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=headers
        )
        assert poll.status_code == 200, poll.text
        job = poll.json()
        assert job["status"] == "complete", job
        assert job["total_steps"] == 1 + 2 * 4
        assert job["completed_steps"] == job["total_steps"]
        assert job["error"] is None
        course_id = job["course_id"]
        assert course_id

    # The fake AI (not a real client) did all the work — no real Anthropic calls.
    assert fake.calls == {"objectives": 1, "content": 4, "assessment": 4}

    # course_id points at a real, populated DraftCourse.
    course = (
        await db_session.execute(
            select(DraftCourse).where(DraftCourse.id == uuid.UUID(course_id))
        )
    ).scalar_one()
    assert course.title == "AI Engineering 101"
    n_obj = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftObjective)
            .where(DraftObjective.draft_course_id == course.id)
        )
    ).scalar_one()
    n_items = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftItem)
            .where(DraftItem.draft_course_id == course.id)
        )
    ).scalar_one()
    assert n_obj == 4
    assert n_items == 8


@pytest.mark.asyncio
async def test_generate_course_async_failure_leaves_no_partial_course(
    db_session: AsyncSession,
):
    fake = FailingObjectivesAuthorAI(count=4)
    async with _transport(db_session, fake) as client:
        headers = _auth("instructor")
        resp = await client.post(
            "/api/v1/builder/generate-course", json=_brief(), headers=headers
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        poll = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=headers
        )
        assert poll.status_code == 200, poll.text
        job = poll.json()
        assert job["status"] == "failed", job
        assert job["error"]
        assert "objectives" in job["error"].lower()
        assert job["course_id"] is None

    # No orphaned partial course anywhere.
    n_courses = (
        await db_session.execute(select(func.count()).select_from(DraftCourse))
    ).scalar_one()
    assert n_courses == 0


@pytest.mark.asyncio
async def test_generate_course_objectives_count_over_20_creates_no_job(
    db_session: AsyncSession,
):
    async with _transport(db_session, FakeAuthorAI()) as client:
        resp = await client.post(
            "/api/v1/builder/generate-course",
            json=_brief(objectives_count=21),
            headers=_auth("instructor"),
        )
    assert resp.status_code == 422, resp.text
    from app.models.generation_job import GenerationJob

    n_jobs = (
        await db_session.execute(select(func.count()).select_from(GenerationJob))
    ).scalar_one()
    assert n_jobs == 0  # 422 fired before any job was created


@pytest.mark.asyncio
async def test_generate_course_non_author_role_is_403(db_session: AsyncSession):
    async with _transport(db_session, FakeAuthorAI()) as client:
        resp = await client.post(
            "/api/v1/builder/generate-course",
            json=_brief(),
            headers=_auth("student"),
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_generate_course_no_api_key_is_503(db_session: AsyncSession):
    """With no override + no API key, get_author_ai must 503 before scheduling."""
    with mock.patch.object(settings, "ANTHROPIC_API_KEY", ""):
        async with _transport(db_session, author_ai=None) as client:
            resp = await client.post(
                "/api/v1/builder/generate-course",
                json=_brief(),
                headers=_auth("instructor"),
            )
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_generation_job_cross_tenant_and_other_user_are_404(
    db_session: AsyncSession,
):
    from tests.conftest import DEFAULT_ORG_ID

    owner_sub = str(uuid.uuid4())
    fake = FakeAuthorAI(count=2)
    async with _transport(db_session, fake) as client:
        owner_headers = _auth("instructor", sub=owner_sub)
        resp = await client.post(
            "/api/v1/builder/generate-course",
            json=_brief(objectives_count=2),
            headers=owner_headers,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        # Owner can read it.
        ok = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=owner_headers
        )
        assert ok.status_code == 200, ok.text

        # Another user in the SAME tenant → 404 (owner-scoped).
        other_user = _auth("instructor", sub=str(uuid.uuid4()), org=DEFAULT_ORG_ID)
        r_other = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=other_user
        )
        assert r_other.status_code == 404, r_other.text

        # A DIFFERENT tenant → 404 (tenant-scoped; the app-layer filter hides it).
        other_org = _auth("instructor", org=uuid.uuid4())
        r_tenant = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=other_org
        )
        assert r_tenant.status_code == 404, r_tenant.text
