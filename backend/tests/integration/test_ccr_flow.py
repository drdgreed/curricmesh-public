"""Integration tests for app/core/workflow/engine.py — full CCR lifecycle.

Uses the db_session fixture (real PostgreSQL).

Task A7 TDD: tests written first to confirm failure, then implemented.
Task A8: updated for server-derived cohort state, target_version_id, author self-approval.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.versioning.semver import BumpType
from app.core.workflow.engine import (
    can_release,
    record_approval,
    record_qa,
    release_ccr,
    submit_ccr,
)
from app.core.workflow.rules import QA_DIMENSIONS, WorkflowError
from app.models.cohort import Cohort
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.history import HistoryEvent
from app.models.user import User
from app.models.version import Version


async def _make_user(session, role: str = "instructor") -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_curriculum(session) -> Curriculum:
    cur = Curriculum(
        name=f"Test Curriculum {uuid.uuid4().hex[:6]}",
        slug=f"test-{uuid.uuid4().hex[:6]}",
    )
    session.add(cur)
    await session.flush()
    return cur


async def _make_version(
    session,
    curriculum_id: uuid.UUID,
    status: LifecycleStatus = LifecycleStatus.approved,
) -> Version:
    v = Version(
        curriculum_id=curriculum_id,
        major=1,
        minor=0,
        patch=0,
        status=status,
    )
    session.add(v)
    await session.flush()
    return v


async def _make_active_cohort(session, curriculum_id: uuid.UUID, version_id: uuid.UUID) -> Cohort:
    """Seed a cohort that is currently active (started yesterday, ends tomorrow)."""
    today = date.today()
    cohort = Cohort(
        curriculum_id=curriculum_id,
        version_id=version_id,
        name="Active Test Cohort",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
    )
    session.add(cohort)
    await session.flush()
    return cohort


# ---------------------------------------------------------------------------
# test_submit_ccr_sets_status_draft_and_records_history
# ---------------------------------------------------------------------------


async def test_submit_ccr_sets_status_draft_and_records_history(db_session):
    """submit_ccr() creates a ChangeRequest with status=draft and records ccr_created event."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(db_session, cur.id, status=LifecycleStatus.approved)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Add new module",
        rationale="Improving coverage",
        proposed_bump=BumpType.minor,
        affected_kinds={AssetKind.slides},
        target_version_id=target_version.id,
    )

    assert ccr.id is not None
    assert ccr.status == LifecycleStatus.draft
    assert ccr.curriculum_id == cur.id
    assert ccr.author_id == author.id
    assert ccr.target_version_id == target_version.id

    # Verify history event was recorded
    result = await db_session.execute(
        select(HistoryEvent).where(HistoryEvent.event_type == "ccr_created")
    )
    events = result.scalars().all()
    assert len(events) >= 1
    # The most recent event should reference our CCR
    target_ids = [e.target for e in events]
    assert any(str(ccr.id) in t for t in target_ids)


# ---------------------------------------------------------------------------
# test_release_blocked_without_qa_pass
# ---------------------------------------------------------------------------


async def test_release_blocked_without_qa_pass(db_session):
    """can_release() returns False when there is no passing QAReview."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(db_session, cur.id, status=LifecycleStatus.approved)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="No QA yet",
        rationale="Testing",
        proposed_bump=BumpType.patch,
        affected_kinds={AssetKind.lab},
        target_version_id=target_version.id,
    )

    # No QA review recorded
    assert await can_release(db_session, ccr) is False


# ---------------------------------------------------------------------------
# test_release_requires_two_approvals_incl_instructor
# ---------------------------------------------------------------------------


async def test_release_requires_two_approvals_incl_instructor(db_session):
    """
    can_release() requires:
      - at least one passing QAReview, AND
      - >=2 Approvals with decision=='approve', of which >=1 has role in
        {instructor, instructor_lead}

    Test the negative cases:
      a) passing QA but only one approval → False
      b) passing QA + two non-instructor approvals → False
      c) passing QA + two approvals incl instructor → True
    """
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    approver1 = await _make_user(db_session, "program_manager")
    approver2 = await _make_user(db_session, "architect")
    instructor = await _make_user(db_session, "instructor_lead")
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(db_session, cur.id, status=LifecycleStatus.approved)

    # -- Case (a): QA pass, one approval
    ccr_a = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Case A",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
        target_version_id=target_version.id,
    )
    full_scores = {dim: 4 for dim in ["content_accuracy", "alignment", "prerequisites",
                                        "consistency", "instructor_support", "student_experience"]}
    await record_qa(db_session, ccr=ccr_a, reviewer_id=reviewer.id,
                    dimension_scores=full_scores, verdict="pass")
    await record_approval(db_session, ccr=ccr_a, approver_id=approver1.id,
                           role="program_manager", decision="approve")
    assert await can_release(db_session, ccr_a) is False

    # -- Case (b): QA pass, two approvals but no instructor role
    ccr_b = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Case B",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
        target_version_id=target_version.id,
    )
    await record_qa(db_session, ccr=ccr_b, reviewer_id=reviewer.id,
                    dimension_scores=full_scores, verdict="pass")
    await record_approval(db_session, ccr=ccr_b, approver_id=approver1.id,
                           role="program_manager", decision="approve")
    await record_approval(db_session, ccr=ccr_b, approver_id=approver2.id,
                           role="architect", decision="approve")
    assert await can_release(db_session, ccr_b) is False

    # -- Case (c): QA pass + two approvals incl instructor_lead → True
    ccr_c = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Case C",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
        target_version_id=target_version.id,
    )
    await record_qa(db_session, ccr=ccr_c, reviewer_id=reviewer.id,
                    dimension_scores=full_scores, verdict="pass")
    await record_approval(db_session, ccr=ccr_c, approver_id=approver1.id,
                           role="program_manager", decision="approve")
    await record_approval(db_session, ccr=ccr_c, approver_id=instructor.id,
                           role="instructor_lead", decision="approve")
    assert await can_release(db_session, ccr_c) is True


# ---------------------------------------------------------------------------
# test_full_ccr_flow
# ---------------------------------------------------------------------------


async def test_full_ccr_flow(db_session):
    """
    Full happy path:
      submit → record_qa(pass) → two approvals incl instructor →
      can_release True → release_ccr activates version, sets ccr approved →
      assert HistoryEvents show the chain.
    """
    # Seed users
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    approver_pm = await _make_user(db_session, "program_manager")
    approver_inst = await _make_user(db_session, "instructor")
    actor = await _make_user(db_session, "program_manager")

    # Seed curriculum + approved version
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(
        db_session, cur.id, status=LifecycleStatus.approved
    )

    # 1. Submit CCR (target_version_id pinned to the approved version)
    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Revamp module 3",
        rationale="Curriculum audit outcome",
        proposed_bump=BumpType.minor,
        affected_kinds={AssetKind.slides, AssetKind.assessment},
        target_version_id=target_version.id,
    )
    assert ccr.status == LifecycleStatus.draft

    # 2. Record passing QA
    full_scores = {
        "content_accuracy": 5,
        "alignment": 4,
        "prerequisites": 4,
        "consistency": 5,
        "instructor_support": 4,
        "student_experience": 5,
    }
    qa = await record_qa(
        db_session,
        ccr=ccr,
        reviewer_id=reviewer.id,
        dimension_scores=full_scores,
        verdict="pass",
    )
    assert qa.id is not None
    assert qa.verdict == "pass"

    # 3. Two approvals — one must be instructor or instructor_lead
    await record_approval(
        db_session,
        ccr=ccr,
        approver_id=approver_pm.id,
        role="program_manager",
        decision="approve",
    )
    await record_approval(
        db_session,
        ccr=ccr,
        approver_id=approver_inst.id,
        role="instructor",
        decision="approve",
    )

    # 4. Gate check
    assert await can_release(db_session, ccr) is True

    # 5. Release — activates the version (no target_version argument: it's on the CCR)
    ccr = await release_ccr(
        db_session,
        ccr=ccr,
        previously_active=None,
        actor_role="program_manager",
        actor_id=actor.id,
    )
    assert ccr.status == LifecycleStatus.approved
    assert target_version.status == LifecycleStatus.active

    # 6. Assert HistoryEvent chain
    result = await db_session.execute(select(HistoryEvent))
    all_events = result.scalars().all()
    event_types = {e.event_type for e in all_events}

    assert "ccr_created" in event_types
    assert "version_active" in event_types


# ---------------------------------------------------------------------------
# test_release_blocked_when_can_release_false
# ---------------------------------------------------------------------------


async def test_release_blocked_when_can_release_false(db_session):
    """release_ccr() raises WorkflowError when the gate conditions are not met."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(db_session, cur.id, status=LifecycleStatus.approved)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Premature release",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
        target_version_id=target_version.id,
    )

    with pytest.raises(WorkflowError, match=r"(?i)release|qa|approv"):
        await release_ccr(
            db_session,
            ccr=ccr,
            previously_active=None,
            actor_role="program_manager",
            actor_id=author.id,
        )


# ---------------------------------------------------------------------------
# test_same_approver_cannot_approve_twice
# ---------------------------------------------------------------------------


async def test_same_approver_cannot_approve_twice(db_session):
    """A second record_approval() call by the same approver raises WorkflowError."""
    author = await _make_user(db_session, "instructor")
    approver = await _make_user(db_session, "program_manager")
    cur = await _make_curriculum(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Dedup test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )

    # First approval succeeds
    await record_approval(
        db_session,
        ccr=ccr,
        approver_id=approver.id,
        role="program_manager",
        decision="approve",
    )

    # Second approval by the same approver must raise
    with pytest.raises(WorkflowError, match=r"(?i)already approved"):
        await record_approval(
            db_session,
            ccr=ccr,
            approver_id=approver.id,
            role="program_manager",
            decision="approve",
        )


# ---------------------------------------------------------------------------
# test_two_approvals_from_same_user_do_not_satisfy_gate
# ---------------------------------------------------------------------------


async def test_two_approvals_from_same_user_do_not_satisfy_gate(db_session):
    """After dedup fix, one approver cannot satisfy the 2-approval gate alone."""
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    approver = await _make_user(db_session, "program_manager")
    cur = await _make_curriculum(db_session)

    full_scores = {dim: 4 for dim in QA_DIMENSIONS}

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Gate dedup test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )
    await record_qa(
        db_session,
        ccr=ccr,
        reviewer_id=reviewer.id,
        dimension_scores=full_scores,
        verdict="pass",
    )

    # Single approval from approver (distinct from author)
    await record_approval(
        db_session,
        ccr=ccr,
        approver_id=approver.id,
        role="program_manager",
        decision="approve",
    )

    # Attempt to approve again (should raise before the gate check)
    with pytest.raises(WorkflowError, match=r"(?i)already approved"):
        await record_approval(
            db_session,
            ccr=ccr,
            approver_id=approver.id,
            role="program_manager",
            decision="approve",
        )

    # Gate should not be satisfied (only 1 unique approval)
    assert await can_release(db_session, ccr) is False


# ---------------------------------------------------------------------------
# test_release_twice_raises_workflow_error
# ---------------------------------------------------------------------------


async def test_release_twice_raises_workflow_error(db_session):
    """Calling release_ccr on an already-released CCR raises WorkflowError."""
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    approver_pm = await _make_user(db_session, "program_manager")
    approver_inst = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    target_version = await _make_version(db_session, cur.id, status=LifecycleStatus.approved)

    full_scores = {dim: 5 for dim in QA_DIMENSIONS}

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Idempotency test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
        target_version_id=target_version.id,
    )
    await record_qa(db_session, ccr=ccr, reviewer_id=reviewer.id,
                    dimension_scores=full_scores, verdict="pass")
    await record_approval(db_session, ccr=ccr, approver_id=approver_pm.id,
                          role="program_manager", decision="approve")
    await record_approval(db_session, ccr=ccr, approver_id=approver_inst.id,
                          role="instructor", decision="approve")

    # First release succeeds
    ccr = await release_ccr(
        db_session,
        ccr=ccr,
        previously_active=None,
        actor_role="program_manager",
        actor_id=approver_pm.id,
    )
    assert ccr.status == LifecycleStatus.approved

    # Second release raises WorkflowError (not IllegalTransition)
    with pytest.raises(WorkflowError, match=r"(?i)already been released"):
        await release_ccr(
            db_session,
            ccr=ccr,
            previously_active=None,
            actor_role="program_manager",
            actor_id=approver_pm.id,
        )


# ---------------------------------------------------------------------------
# test_qa_fail_with_out_of_range_score_rejected
# ---------------------------------------------------------------------------


async def test_qa_fail_with_out_of_range_score_rejected(db_session):
    """record_qa(verdict='fail', dimension_scores={'content_accuracy': 99}) raises WorkflowError."""
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    cur = await _make_curriculum(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Out-of-range fail test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )

    with pytest.raises(WorkflowError, match=r"(?i)score|range|1.*5"):
        await record_qa(
            db_session,
            ccr=ccr,
            reviewer_id=reviewer.id,
            dimension_scores={"content_accuracy": 99},
            verdict="fail",
        )


# ---------------------------------------------------------------------------
# test_verdict_normalized
# ---------------------------------------------------------------------------


async def test_verdict_normalized(db_session):
    """record_qa(verdict='PASS', all_dims...) works after case normalization."""
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    cur = await _make_curriculum(db_session)

    full_scores = {dim: 3 for dim in QA_DIMENSIONS}

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Normalization test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )

    # 'PASS' should be normalized to 'pass' and succeed
    qa = await record_qa(
        db_session,
        ccr=ccr,
        reviewer_id=reviewer.id,
        dimension_scores=full_scores,
        verdict="PASS",
    )
    assert qa.verdict == "pass"

    # An invalid verdict should raise WorkflowError
    ccr2 = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Invalid verdict test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )
    with pytest.raises(WorkflowError, match=r"(?i)invalid verdict"):
        await record_qa(
            db_session,
            ccr=ccr2,
            reviewer_id=reviewer.id,
            dimension_scores=full_scores,
            verdict="APPROVE",
        )


# ---------------------------------------------------------------------------
# test_author_self_approval_raises (C)
# ---------------------------------------------------------------------------


async def test_author_self_approval_raises(db_session):
    """The CCR author may not approve their own change request — raises WorkflowError."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)

    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Self-approval test",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )

    with pytest.raises(WorkflowError, match=r"(?i)author.*approve|may not approve"):
        await record_approval(
            db_session,
            ccr=ccr,
            approver_id=author.id,
            role="instructor",
            decision="approve",
        )


# ---------------------------------------------------------------------------
# test_mid_cohort_rule_fires_with_active_cohort (A)
# ---------------------------------------------------------------------------


async def test_mid_cohort_rule_fires_with_active_cohort(db_session):
    """submit_ccr() raises WorkflowError for minor/major bump when an active cohort exists in DB."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    # Seed a version to attach the cohort to
    version = await _make_version(db_session, cur.id, status=LifecycleStatus.active)
    # Seed an active cohort
    await _make_active_cohort(db_session, cur.id, version.id)

    # Minor bump mid-cohort should raise
    with pytest.raises(WorkflowError, match=r"(?i)patch|mid.cohort|cohort"):
        await submit_ccr(
            db_session,
            curriculum_id=cur.id,
            author_id=author.id,
            title="Mid-cohort minor bump",
            rationale="",
            proposed_bump=BumpType.minor,
            affected_kinds=set(),
        )


async def test_patch_bump_allowed_mid_cohort(db_session):
    """submit_ccr() allows patch bump even when an active cohort exists."""
    author = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)
    version = await _make_version(db_session, cur.id, status=LifecycleStatus.active)
    await _make_active_cohort(db_session, cur.id, version.id)

    # Patch bump mid-cohort should succeed
    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="Patch mid-cohort",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )
    assert ccr.status == LifecycleStatus.draft


# ---------------------------------------------------------------------------
# test_target_version_id_release_flow (B)
# ---------------------------------------------------------------------------


async def test_release_without_target_version_raises(db_session):
    """release_ccr() raises WorkflowError when CCR has no target_version_id."""
    author = await _make_user(db_session, "instructor")
    reviewer = await _make_user(db_session, "qa_lead")
    approver_pm = await _make_user(db_session, "program_manager")
    approver_inst = await _make_user(db_session, "instructor")
    cur = await _make_curriculum(db_session)

    full_scores = {dim: 5 for dim in QA_DIMENSIONS}

    # Submit CCR with no target_version_id
    ccr = await submit_ccr(
        db_session,
        curriculum_id=cur.id,
        author_id=author.id,
        title="No target version",
        rationale="",
        proposed_bump=BumpType.patch,
        affected_kinds=set(),
    )
    await record_qa(db_session, ccr=ccr, reviewer_id=reviewer.id,
                    dimension_scores=full_scores, verdict="pass")
    await record_approval(db_session, ccr=ccr, approver_id=approver_pm.id,
                          role="program_manager", decision="approve")
    await record_approval(db_session, ccr=ccr, approver_id=approver_inst.id,
                          role="instructor", decision="approve")

    with pytest.raises(WorkflowError, match=r"(?i)no approved target version"):
        await release_ccr(
            db_session,
            ccr=ccr,
            previously_active=None,
            actor_role="program_manager",
            actor_id=approver_pm.id,
        )
