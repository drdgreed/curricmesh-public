"""Model tests for GenerationJob (async course-generation job tracking).

Proves the row persists with the right defaults, is write-stamped with the
ambient org (TenantScoped), and links to a DraftCourse on success.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.models import DraftCourse
from app.models.generation_job import GenerationJob
from tests.conftest import DEFAULT_ORG_ID


@pytest.mark.asyncio
async def test_generation_job_defaults_and_org_stamp(db_session: AsyncSession):
    job = GenerationJob(total_steps=9, created_by=uuid.uuid4())
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    assert job.id is not None
    assert job.status == "pending"  # column default
    assert job.completed_steps == 0  # column default
    assert job.total_steps == 9
    assert job.phase is None
    assert job.course_id is None
    assert job.error is None
    assert job.created_at is not None
    assert job.updated_at is not None
    # TenantScoped: write-stamped from the ambient org context (require_org()).
    assert job.organization_id == DEFAULT_ORG_ID


@pytest.mark.asyncio
async def test_generation_job_completion_links_course(db_session: AsyncSession):
    course = DraftCourse(title="Async course", description="t", status="drafting")
    db_session.add(course)
    await db_session.flush()

    job = GenerationJob(total_steps=3, created_by=uuid.uuid4())
    db_session.add(job)
    await db_session.flush()

    job.status = "complete"
    job.completed_steps = 3
    job.phase = "complete"
    job.course_id = course.id
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(GenerationJob).where(GenerationJob.id == job.id)
        )
    ).scalar_one()
    assert fetched.status == "complete"
    assert fetched.completed_steps == 3
    assert fetched.course_id == course.id


@pytest.mark.asyncio
async def test_generation_job_failure_records_error(db_session: AsyncSession):
    job = GenerationJob(total_steps=5, created_by=uuid.uuid4())
    db_session.add(job)
    await db_session.flush()

    job.status = "failed"
    job.error = "boom: generate_objectives raised"
    await db_session.commit()
    await db_session.refresh(job)

    assert job.status == "failed"
    assert job.error == "boom: generate_objectives raised"
    assert job.course_id is None  # no partial course leaked
