"""Runner test exercising the PRODUCTION org-scope seam (org_scoped_session).

The endpoint tests inject the test session into the runner; this test drives the
REAL default ``session_scope=org_scoped_session`` so the runner opens its OWN
session and establishes the tenant context itself (ContextVar + app.current_org
GUC), exactly as it does outside a request. It proves the assembled course is
write-stamped with the runner's org — i.e. cross-tenant isolation holds even
though the runner runs with no ambient request context.

The app-engine connection is disposed at the end so its loop-bound pool never
leaks into another (function-scoped) test loop.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.course_generator import CourseBrief
from app.builder.generation_runner import run_generation
from app.builder.models import DraftCourse
from app.database import engine as app_engine
from app.models.generation_job import GenerationJob
from tests.builder.test_course_generator import FakeAuthorAI
from tests.conftest import DEFAULT_ORG_ID


@pytest.mark.asyncio
async def test_run_generation_sets_own_org_scope(db_session: AsyncSession):
    # This is the only test that drives the module-level app engine (via the
    # production org_scoped_session). pytest-asyncio uses a fresh event loop per
    # test, so any connection an earlier test left pooled is bound to a now-dead
    # loop. Dispose the pool first so org_scoped_session builds fresh connections
    # on THIS loop, and again at the end so we leave nothing bound to this loop.
    await app_engine.dispose()

    # A pending job under DEFAULT_ORG (db_session is org-scoped by conftest).
    author_id = uuid.uuid4()
    job = GenerationJob(total_steps=1 + 2 * 2, created_by=author_id)
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    brief = CourseBrief(
        title="Runner course",
        topic="Own-session org scope",
        target_weeks=2,
        objectives_count=2,
    )

    try:
        # REAL production seam: no session_scope override → org_scoped_session.
        # The runner must set its own org context on its own connection.
        await run_generation(
            job_id,
            brief,
            author_id,
            DEFAULT_ORG_ID,
            author_ai=FakeAuthorAI(count=2),
        )

        # The runner committed on its OWN (app-engine) connection. Expire the
        # test session's identity map so the ORM re-reads the row fresh (READ
        # COMMITTED) rather than returning the stale cached instance.
        db_session.expire_all()
        fetched = (
            await db_session.execute(
                select(GenerationJob).where(GenerationJob.id == job_id)
            )
        ).scalar_one()
        assert fetched.status == "complete"
        assert fetched.course_id is not None
        assert fetched.completed_steps == fetched.total_steps

        course = (
            await db_session.execute(
                select(DraftCourse).where(DraftCourse.id == fetched.course_id)
            )
        ).scalar_one()
        # The runner established the org context on its OWN session, so the course
        # is write-stamped with that tenant — isolation holds.
        assert course.organization_id == DEFAULT_ORG_ID
        assert course.title == "Runner course"
    finally:
        # Dispose the loop-bound app-engine pool so it can't leak into the next
        # function-scoped test loop.
        await app_engine.dispose()
