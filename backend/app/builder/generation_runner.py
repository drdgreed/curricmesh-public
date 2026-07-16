"""Background runner for async full-course generation.

``POST /api/v1/builder/generate-course`` no longer blocks on the minutes-long
orchestration (``1 + 2 * objectives_count`` sequential ~29s AI calls). It creates
a ``GenerationJob`` (``pending``), returns 202 + ``job_id``, and schedules
:func:`run_generation` on a FastAPI ``BackgroundTask``. This module is that task.

Because the task runs OUTSIDE the request, it CANNOT reuse the request session
(closed once the response is sent) and it must establish tenant scope on its own
session — the request's ``tenant_context`` dependency does not reach here. It
does so through the injected ``session_scope`` factory, which in production is
``app.database.org_scoped_session``: that sets BOTH the ``current_org``
ContextVar (app-layer auto-filter) AND the Postgres ``app.current_org`` GUC on
every transaction begin (DB-layer FORCE-RLS), so every read/write is pinned to
``org_id``. Cross-tenant isolation therefore holds exactly as it does inside a
request. Tests inject a factory yielding an already-org-scoped session (so no
real DB connection is opened on the app engine), and inject a fake
``author_ai`` — ZERO real Anthropic calls in CI.

Failure contract: ``generate_course`` assembles and commits the whole course in
one final transaction, so if it raises mid-flight nothing course-related is
committed. The runner additionally rolls back before marking the job ``failed``,
guaranteeing no orphaned partial course is ever left behind — only the job row
(with ``error`` set, ``course_id`` still NULL) records the failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import CourseAuthorAI
from app.builder.course_generator import CourseBrief, generate_course
from app.database import org_scoped_session
from app.models.generation_job import GenerationJob

# A factory called as ``async with session_scope(org_id) as session:`` yielding
# an org-scoped ``AsyncSession``. Production default: ``org_scoped_session``.
SessionScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


async def run_generation(
    job_id: uuid.UUID,
    brief: CourseBrief,
    author_id: uuid.UUID | None,
    org_id: uuid.UUID,
    *,
    author_ai: CourseAuthorAI,
    session_scope: SessionScope = org_scoped_session,
) -> None:
    """Run one course generation to completion, updating its GenerationJob.

    Opens an org-scoped session, marks the job ``running``, drives
    ``generate_course`` with a progress callback that persists
    ``completed_steps``/``phase`` (committing each update so pollers see live
    progress), and finishes ``complete`` (with ``course_id``) or ``failed`` (with
    ``error`` and no partial course). Never raises: a background task has no
    caller to surface an exception to — the failure is recorded on the job.
    """
    async with session_scope(org_id) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:  # job vanished / wrong tenant — nothing to do.
            return

        job.status = "running"
        await session.commit()

        async def on_progress(completed: int, total: int, phase: str) -> None:
            # Persist progress so GET /jobs/{id} reflects it live. Safe to commit
            # mid-generation: generate_course does no DB writes before assembly,
            # so only this job row is flushed here.
            job.completed_steps = completed
            job.phase = phase
            await session.commit()

        try:
            result = await generate_course(
                session,
                brief=brief,
                author_ai=author_ai,
                author_id=author_id,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — record on the job, never leak a partial course
            # Roll back any uncommitted objects generate_course had staged, so no
            # orphaned partial course survives, then mark the job failed.
            await session.rollback()
            job = await session.get(GenerationJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = str(exc) or exc.__class__.__name__
                await session.commit()
            return

        job.course_id = result.course.id
        job.completed_steps = job.total_steps
        job.phase = "complete"
        job.status = "complete"
        await session.commit()
