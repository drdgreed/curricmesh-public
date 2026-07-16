"""Model tests for the learner-delivery tables (Phase 2, Foundation 1).

Exercise persistence, tenant stamping, the version-pin immutability invariant,
and the uniqueness constraints — against the live RLS'd test DB (db_session).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.learner import AssessmentSubmission, Enrollment, LearnerProgress
from tests.conftest import DEFAULT_ORG_ID


async def _released_version_with_item(
    db: AsyncSession,
) -> tuple[CurriculumVersion, VersionMember]:
    """Build a released CurriculumVersion carrying one renderable item."""
    curriculum = Curriculum(name="Agentic AI", slug=f"agentic-{uuid.uuid4()}")
    db.add(curriculum)
    await db.flush()

    version = CurriculumVersion(
        curriculum_id=curriculum.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,  # "active" == released
    )
    db.add(version)
    await db.flush()

    asset = LineageAsset(kind=AssetKind.lesson_plan, lineage_key="intro")
    db.add(asset)
    await db.flush()

    content = ContentVersion(
        asset_id=asset.id,
        seq=1,
        content="Welcome to the course.",
        content_hash="a" * 64,
    )
    db.add(content)
    await db.flush()

    member = VersionMember(
        curriculum_version_id=version.id,
        asset_id=asset.id,
        asset_version_id=content.id,
        section="Week 1",
        week_index=0,
        order=0,
    )
    db.add(member)
    await db.flush()
    return version, member


@pytest.mark.asyncio
async def test_enrollment_persists_and_is_tenant_stamped(db_session: AsyncSession):
    version, _ = await _released_version_with_item(db_session)
    learner_id = uuid.uuid4()

    enrollment = Enrollment(
        learner_id=learner_id, curriculum_version_id=version.id
    )
    db_session.add(enrollment)
    await db_session.commit()
    await db_session.refresh(enrollment)

    assert enrollment.id is not None
    # Tenant stamped from ambient org context (fail-closed default).
    assert enrollment.organization_id == DEFAULT_ORG_ID
    # Default status + the pinned version.
    assert enrollment.status == "active"
    assert enrollment.curriculum_version_id == version.id
    assert enrollment.completed_at is None


@pytest.mark.asyncio
async def test_enrollment_pins_version_immutably(db_session: AsyncSession):
    """The enrolled version id is the pin — it does not track re-releases.

    Creating a *newer* released version for the same curriculum leaves the
    existing enrollment pointing at the originally-pinned version.
    """
    v1, _ = await _released_version_with_item(db_session)
    enrollment = Enrollment(learner_id=uuid.uuid4(), curriculum_version_id=v1.id)
    db_session.add(enrollment)
    await db_session.commit()

    # A later re-release: a brand-new CurriculumVersion for the same curriculum.
    v2 = CurriculumVersion(
        curriculum_id=v1.curriculum_id,
        major=2,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
        parent_version_id=v1.id,
    )
    db_session.add(v2)
    await db_session.commit()
    await db_session.refresh(enrollment)

    # The pin is unchanged — immutability carries through to the learner.
    assert enrollment.curriculum_version_id == v1.id
    assert enrollment.curriculum_version_id != v2.id


@pytest.mark.asyncio
async def test_enrollment_unique_per_learner_version(db_session: AsyncSession):
    version, _ = await _released_version_with_item(db_session)
    learner_id = uuid.uuid4()
    db_session.add(
        Enrollment(learner_id=learner_id, curriculum_version_id=version.id)
    )
    await db_session.commit()

    db_session.add(
        Enrollment(learner_id=learner_id, curriculum_version_id=version.id)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_progress_persists_and_unique_per_item(db_session: AsyncSession):
    version, member = await _released_version_with_item(db_session)
    enrollment = Enrollment(
        learner_id=uuid.uuid4(), curriculum_version_id=version.id
    )
    db_session.add(enrollment)
    await db_session.flush()

    progress = LearnerProgress(
        enrollment_id=enrollment.id, content_member_id=member.id
    )
    db_session.add(progress)
    await db_session.commit()
    await db_session.refresh(progress)

    assert progress.organization_id == DEFAULT_ORG_ID
    assert progress.status == "not_started"

    # One row per (enrollment, item).
    db_session.add(
        LearnerProgress(enrollment_id=enrollment.id, content_member_id=member.id)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_assessment_submission_persists(db_session: AsyncSession):
    version, member = await _released_version_with_item(db_session)
    enrollment = Enrollment(
        learner_id=uuid.uuid4(), curriculum_version_id=version.id
    )
    db_session.add(enrollment)
    await db_session.flush()

    sub = AssessmentSubmission(
        enrollment_id=enrollment.id,
        content_member_id=member.id,
        response_text="My answer.",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    assert sub.organization_id == DEFAULT_ORG_ID
    assert sub.submitted_at is not None
    # Score/feedback null in v1 (populated by Phase B).
    assert sub.score is None
    assert sub.feedback is None

    # Re-fetch to prove it's queryable within the tenant.
    result = await db_session.execute(
        select(AssessmentSubmission).where(AssessmentSubmission.id == sub.id)
    )
    assert result.scalar_one().response_text == "My answer."
