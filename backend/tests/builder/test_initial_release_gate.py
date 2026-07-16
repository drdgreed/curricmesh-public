"""Slice 5 — the activation gate for a first-time authored course.

``publish_draft`` assembles a pre-active candidate ``CurriculumVersion`` (status
``review``) + an initial-release ``ChangeRequest``. This module proves the ONLY
path from that candidate to ``active`` is
:func:`app.core.workflow.engine.activate_initial_release`, and that it enforces
the SAME mandatory gate as every other release:

  * blocked before any passing QA review;
  * blocked with a QA pass but fewer than two approvals;
  * blocked with two approvals but none from an instructor;
  * succeeds only with a QA pass + two approvals including an instructor — and
    then the candidate is active + the curriculum points at it;
  * a second activation is rejected (idempotency).

Fixture style mirrors ``tests/builder/test_compile.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.compile import publish_draft
from app.builder.models import (
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.config import settings
from app.core.manifest import active_curriculum_version
from app.core.workflow.engine import (
    activate_initial_release,
    record_approval,
    record_qa,
)
from app.core.workflow.rules import QA_DIMENSIONS, WorkflowError
from app.database import Base
from app.db.rls import apply_rls
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.org import Organization
from app.models.user import User
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

_QA_PASS_SCORES = {dim: 5 for dim in QA_DIMENSIONS}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def rls_engine():
    """Fresh, RLS-enabled schema — owned by this test module."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _org_and_users(engine) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Seed an org + an author and two distinct approvers (one instructor)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Test Org")
        s.add(org)
        await s.flush()
        author = User(email="author@test.com", role="architect", organization_id=org.id)
        instructor = User(
            email="instructor@test.com", role="instructor", organization_id=org.id
        )
        pm = User(email="pm@test.com", role="program_manager", organization_id=org.id)
        arch = User(email="arch2@test.com", role="architect", organization_id=org.id)
        s.add_all([author, instructor, pm, arch])
        await s.commit()
        return org.id, {
            "author": author.id,
            "instructor": instructor.id,
            "pm": pm.id,
            "arch2": arch.id,
        }


async def _open_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _build_draft(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """A minimal valid draft: 1 objective + 1 aligned item."""
    course = DraftCourse(organization_id=org_id, title="Gate Course", status="drafting")
    session.add(course)
    await session.flush()
    obj = DraftObjective(
        organization_id=org_id,
        draft_course_id=course.id,
        text="Understand the gate",
        week_index=1,
        order_index=0,
    )
    session.add(obj)
    await session.flush()
    item = DraftItem(
        organization_id=org_id,
        draft_course_id=course.id,
        kind=AssetKind.lesson_plan,
        title="Gate lesson",
        content="body",
        week_index=1,
        order_index=0,
    )
    session.add(item)
    await session.flush()
    session.add(
        DraftItemObjective(
            organization_id=org_id,
            draft_item_id=item.id,
            draft_objective_id=obj.id,
        )
    )
    await session.commit()
    return course.id


async def _publish_candidate(session, org_id, users):
    """Publish a draft to a candidate + return its initial-release CCR."""
    draft_id = await _build_draft(session, org_id)
    result = await publish_draft(session, draft_id, author_id=users["author"])
    await session.commit()
    return result.ccr, result.version


async def _assert_not_active(session, curriculum_id, version_id):
    """The candidate is still review + the curriculum has no active version.

    Takes plain ids (not ORM objects) so it is safe to call after a rollback,
    which would otherwise expire attributes on detached instances.
    """
    cv = await session.get(CurriculumVersion, version_id)
    assert cv.status == LifecycleStatus.review
    curriculum = await session.get(Curriculum, curriculum_id)
    assert curriculum.active_content_version_id is None
    assert await active_curriculum_version(session, curriculum_id) is None


# ---------------------------------------------------------------------------
# Tests — the gate must block every incomplete path
# ---------------------------------------------------------------------------


async def test_activation_blocked_before_qa(rls_engine):
    """No QA pass, no approvals → activation blocked; nothing goes active."""
    org_id, users = await _org_and_users(rls_engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                ccr, version = await _publish_candidate(session, org_id, users)
                curriculum_id, version_id = ccr.curriculum_id, version.id

                with pytest.raises(WorkflowError):
                    await activate_initial_release(
                        session, ccr=ccr, actor_id=users["pm"]
                    )
                await session.rollback()
                await _assert_not_active(session, curriculum_id, version_id)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_activation_blocked_with_qa_but_too_few_approvals(rls_engine):
    """QA pass but only one approval → still blocked (gate needs >= 2)."""
    org_id, users = await _org_and_users(rls_engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                ccr, version = await _publish_candidate(session, org_id, users)
                curriculum_id, version_id = ccr.curriculum_id, version.id

                await record_qa(
                    session,
                    ccr=ccr,
                    reviewer_id=None,
                    dimension_scores=_QA_PASS_SCORES,
                    verdict="pass",
                )
                await record_approval(
                    session,
                    ccr=ccr,
                    approver_id=users["instructor"],
                    role="instructor",
                    decision="approve",
                )
                await session.commit()

                with pytest.raises(WorkflowError):
                    await activate_initial_release(
                        session, ccr=ccr, actor_id=users["pm"]
                    )
                await session.rollback()
                await _assert_not_active(session, curriculum_id, version_id)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_activation_blocked_without_instructor_approval(rls_engine):
    """QA pass + two approvals but none from an instructor → blocked."""
    org_id, users = await _org_and_users(rls_engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                ccr, version = await _publish_candidate(session, org_id, users)
                curriculum_id, version_id = ccr.curriculum_id, version.id

                await record_qa(
                    session,
                    ccr=ccr,
                    reviewer_id=None,
                    dimension_scores=_QA_PASS_SCORES,
                    verdict="pass",
                )
                # Two approvals, both non-instructor roles, both distinct from the
                # author (who may not approve their own CCR).
                await record_approval(
                    session,
                    ccr=ccr,
                    approver_id=users["pm"],
                    role="program_manager",
                    decision="approve",
                )
                await record_approval(
                    session,
                    ccr=ccr,
                    approver_id=users["arch2"],  # architect, distinct approver
                    role="architect",
                    decision="approve",
                )
                await session.commit()

                with pytest.raises(WorkflowError):
                    await activate_initial_release(
                        session, ccr=ccr, actor_id=users["pm"]
                    )
                await session.rollback()
                await _assert_not_active(session, curriculum_id, version_id)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_activation_succeeds_with_full_gate(rls_engine):
    """QA pass + two approvals incl. instructor → candidate active + pointer set."""
    org_id, users = await _org_and_users(rls_engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                ccr, version = await _publish_candidate(session, org_id, users)

                await record_qa(
                    session,
                    ccr=ccr,
                    reviewer_id=None,
                    dimension_scores=_QA_PASS_SCORES,
                    verdict="pass",
                )
                await record_approval(
                    session,
                    ccr=ccr,
                    approver_id=users["instructor"],
                    role="instructor",
                    decision="approve",
                )
                await record_approval(
                    session,
                    ccr=ccr,
                    approver_id=users["pm"],
                    role="program_manager",
                    decision="approve",
                )
                await session.commit()

                activated = await activate_initial_release(
                    session, ccr=ccr, actor_id=users["pm"]
                )
                await session.commit()

                # The candidate is now the active version.
                cv = await session.get(CurriculumVersion, version.id)
                assert cv.status == LifecycleStatus.active
                assert activated.id == version.id

                # The curriculum points at it.
                curriculum = await session.get(Curriculum, ccr.curriculum_id)
                assert curriculum.active_content_version_id == version.id
                resolved = await active_curriculum_version(session, ccr.curriculum_id)
                assert resolved is not None and resolved.id == version.id

                # The CCR is terminal.
                await session.refresh(ccr)
                assert ccr.status == LifecycleStatus.active

                # Idempotency: a second activation is rejected.
                with pytest.raises(WorkflowError):
                    await activate_initial_release(
                        session, ccr=ccr, actor_id=users["pm"]
                    )
            finally:
                await session.close()
    finally:
        current_org.reset(token)
