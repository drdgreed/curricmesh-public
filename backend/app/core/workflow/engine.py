"""Async DB-backed workflow orchestration for CurricMesh — Task A7.

Responsibilities:
  - submit_ccr:     Run rule guards, create a ChangeRequest, record history.
  - record_qa:      Validate QA completeness, persist QAReview, record history.
  - record_approval: Persist an Approval row.
  - can_release:    Query DB to check the release gate (QA pass + 2 approvals
                    incl. instructor).
  - release_ccr:    Check gate, activate the target version via lifecycle.activate(),
                    persist lifecycle events, set CCR status to approved.

Design notes:
  - All functions are async and accept an SQLAlchemy AsyncSession.
  - They call session.flush() (via helper calls) but never session.commit().
    The caller controls the transaction boundary.
  - Rule guards come from rules.py (pure). DB queries are here.
  - Lifecycle transitions and history persistence reuse lifecycle.activate()
    and history.persist_event() exactly as specified.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.history import EventType, persist_event, record
from app.core.versioning.lifecycle import activate
from app.core.versioning.semver import BumpType
from app.core.workflow.rules import (
    WorkflowError,
    assert_lo_change_includes_assessment,
    assert_patch_only_mid_cohort,
    assert_qa_complete,
)
from app.models.cohort import Cohort
from app.models.enums import AssetKind, LifecycleStatus
from app.models.workflow import Approval, ChangeRequest, QAReview

if TYPE_CHECKING:
    from app.models.version import Version


# ---------------------------------------------------------------------------
# submit_ccr
# ---------------------------------------------------------------------------


async def submit_ccr(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    author_id: uuid.UUID | None,
    title: str,
    rationale: str | None,
    proposed_bump: BumpType,
    affected_kinds: set[AssetKind],
    instructor_override: bool = False,
    target_version_id: uuid.UUID | None = None,
    affected_asset_ids: list[uuid.UUID] | None = None,
    external_link: str | None = None,
) -> ChangeRequest:
    """Create a ChangeRequest after validating workflow pre-conditions.

    Guards:
      - assert_patch_only_mid_cohort: prevent minor/major bumps mid-cohort.
        Active cohort state is DERIVED from the DB (not trusted from client).
      - When affected_asset_ids is NOT provided: assert_lo_change_includes_assessment
        structural kind-level guard (backward-compatible path).
      - When affected_asset_ids IS provided: cascade-driven enforcement —
        if any affected LO asset cascades to an assessment not in affected_asset_ids,
        raise WorkflowError (rule 2.2 enforced via actual dependency graph).

    Args:
        session:              Active AsyncSession.
        curriculum_id:        FK to the curriculum being changed.
        author_id:            FK to the user creating the CCR.
        title:                Short description of the change.
        rationale:            Explanation of why the change is needed.
        proposed_bump:        BumpType indicating the version bump magnitude.
        affected_kinds:       Set of AssetKind values affected by this change.
        instructor_override:  Bypass the mid-cohort minor/major guard.
        target_version_id:    Optional UUID of the version to release with this CCR.
        affected_asset_ids:   Optional list of specific Asset UUIDs being changed.
                              When provided, activates cascade impact analysis and
                              graph-based LO→assessment enforcement.
        external_link:        Optional URL for a Jira/GitHub issue or other tracker.

    Returns:
        The newly created and flushed ChangeRequest with status=draft.

    Raises:
        WorkflowError: if any rule guard fails.
    """
    from app.core.cascade.engine import cascade
    from app.models.graph import DependencyEdge
    from app.models.structure import Asset

    # Derive active-cohort state from DB — never trust the client.
    today = date.today()
    cohort_result = await session.execute(
        select(Cohort).where(
            Cohort.curriculum_id == curriculum_id,
            Cohort.start_date <= today,
            (Cohort.end_date.is_(None)) | (Cohort.end_date >= today),
        )
    )
    has_active_cohort = cohort_result.scalars().first() is not None

    # Rule: mid-cohort bump guard always applies
    assert_patch_only_mid_cohort(proposed_bump, has_active_cohort, instructor_override)

    # Impact computation and LO→assessment enforcement
    impact: dict | None = None

    if affected_asset_ids is not None:
        # --- Graph-driven path (B2) ---
        affected_set = set(affected_asset_ids)

        # Load all DependencyEdge rows ONCE, then call the pure cascade() per
        # affected asset — eliminates the N+1 that loaded the full edge table
        # once per asset in the old cascade_for_asset() loop.
        edge_result = await session.execute(select(DependencyEdge))
        all_edges = edge_result.scalars().all()

        # Compute cascade for each affected asset, dedupe by asset_id,
        # excluding the affected assets themselves.
        seen_bump_ids: set[uuid.UUID] = set()
        all_bumps = []
        for asset_id in affected_asset_ids:
            for bump in cascade(asset_id, all_edges):
                if bump.asset_id not in seen_bump_ids and bump.asset_id not in affected_set:
                    seen_bump_ids.add(bump.asset_id)
                    all_bumps.append(bump)

        # Determine coverage.
        # cascaded_ids already excludes affected_set (filtered in the loop above),
        # so the subtraction is a no-op. Keep direct assignment for clarity.
        cascaded_ids = {b.asset_id for b in all_bumps}
        uncovered_ids = cascaded_ids
        fully_covered = len(uncovered_ids) == 0

        # Load kinds for affected assets and cascaded dependents to enforce rule 2.2
        assets_to_check = affected_set | cascaded_ids
        if assets_to_check:
            asset_result = await session.execute(
                select(Asset).where(Asset.id.in_(assets_to_check))
            )
            asset_kind_map: dict[uuid.UUID, AssetKind] = {
                a.id: a.kind for a in asset_result.scalars().all()
            }
        else:
            asset_kind_map = {}

        # Rule 2.2 via the real graph: if any affected asset is a LO, and
        # the cascade proposes bumping a dependent assessment NOT in affected_set,
        # that is a violation.
        lo_asset_ids = {
            aid for aid in affected_set
            if asset_kind_map.get(aid) == AssetKind.learning_objectives
        }
        if lo_asset_ids:
            uncovered_assessment_ids = {
                bump.asset_id for bump in all_bumps
                if bump.asset_id not in affected_set
                and asset_kind_map.get(bump.asset_id) == AssetKind.assessment
            }
            if uncovered_assessment_ids:
                raise WorkflowError(
                    "a learning-objectives change must include the dependent assessment bump"
                )

        impact = {
            "cascade": [
                {
                    "asset_id": str(b.asset_id),
                    "bump_type": b.bump_type.value,
                    "reason": b.reason,
                }
                for b in all_bumps
            ],
            "affected_asset_ids": [str(aid) for aid in affected_asset_ids],
            "fully_covered": fully_covered,
            "uncovered_asset_ids": [str(aid) for aid in uncovered_ids],
        }
    else:
        # --- Structural guard (backward-compatible path) ---
        assert_lo_change_includes_assessment(affected_kinds)

    # Create CCR
    ccr = ChangeRequest(
        curriculum_id=curriculum_id,
        author_id=author_id,
        title=title,
        rationale=rationale,
        proposed_bump=proposed_bump.value,
        status=LifecycleStatus.draft,
        target_version_id=target_version_id,
        external_link=external_link,
        impact=impact,
    )
    session.add(ccr)
    await session.flush()

    # Record domain event
    await record(
        session,
        actor_id=author_id,
        event_type=EventType.ccr_created,
        target=f"ccr:{ccr.id}",
        details={
            "curriculum_id": str(curriculum_id),
            "proposed_bump": proposed_bump.value,
            "title": title,
        },
    )

    return ccr


# ---------------------------------------------------------------------------
# record_qa
# ---------------------------------------------------------------------------


async def record_qa(
    session: AsyncSession,
    *,
    ccr: ChangeRequest,
    reviewer_id: uuid.UUID | None,
    dimension_scores: dict,
    verdict: str,
) -> QAReview:
    """Validate and persist a QAReview for a ChangeRequest.

    Args:
        session:          Active AsyncSession.
        ccr:              The ChangeRequest being reviewed.
        reviewer_id:      FK to the reviewing user.
        dimension_scores: Dict of dimension name → integer score (1–5).
        verdict:          'pass' or 'fail'.

    Returns:
        The newly created and flushed QAReview.

    Raises:
        WorkflowError: if the QA data does not satisfy completeness rules,
                       or if verdict is not 'pass' or 'fail'.
    """
    # Normalize verdict: strip whitespace, lowercase
    verdict = verdict.strip().lower()
    if verdict not in {"pass", "fail"}:
        raise WorkflowError(
            f"Invalid verdict '{verdict}'. Must be 'pass' or 'fail'."
        )

    assert_qa_complete(dimension_scores, verdict)

    qa = QAReview(
        ccr_id=ccr.id,
        reviewer_id=reviewer_id,
        dimension_scores=dimension_scores,
        verdict=verdict,
    )
    session.add(qa)
    await session.flush()

    # Record history event when verdict is a pass
    if verdict == "pass":
        await record(
            session,
            actor_id=reviewer_id,
            event_type=EventType.qa_passed,
            target=f"ccr:{ccr.id}",
            details={"qa_review_id": str(qa.id), "verdict": verdict},
        )

    return qa


# ---------------------------------------------------------------------------
# record_approval
# ---------------------------------------------------------------------------


async def record_approval(
    session: AsyncSession,
    *,
    ccr: ChangeRequest,
    approver_id: uuid.UUID | None,
    role: str,
    decision: str,
) -> Approval:
    """Persist an Approval for a ChangeRequest.

    Args:
        session:     Active AsyncSession.
        ccr:         The ChangeRequest being approved.
        approver_id: FK to the approving user.
        role:        The approver's role string.
        decision:    'approve' or 'reject'.

    Returns:
        The newly created and flushed Approval.

    Raises:
        WorkflowError: if decision is invalid, the CCR author tries to approve
                       their own CCR, or the same approver has already approved.
    """
    # Normalize inputs
    decision = decision.strip().lower()
    role = role.strip().lower()
    if decision not in {"approve", "reject"}:
        raise WorkflowError(
            f"Invalid decision '{decision}'. Must be 'approve' or 'reject'."
        )

    # Author self-approval guard
    if approver_id is not None and ccr.author_id is not None and approver_id == ccr.author_id:
        raise WorkflowError("CCR author may not approve their own change request")

    # Distinct-approver check: prevent same user from approving twice
    if approver_id is not None:
        existing = await session.execute(
            select(Approval).where(
                Approval.ccr_id == ccr.id,
                Approval.approver_id == approver_id,
            )
        )
        if existing.scalars().first() is not None:
            raise WorkflowError(
                f"Approver {approver_id} has already approved this CCR"
            )

    approval = Approval(
        ccr_id=ccr.id,
        approver_id=approver_id,
        role=role,
        decision=decision,
    )
    session.add(approval)
    await session.flush()
    return approval


# ---------------------------------------------------------------------------
# can_release
# ---------------------------------------------------------------------------

_INSTRUCTOR_ROLES = frozenset({"instructor", "instructor_lead"})


async def can_release(session: AsyncSession, ccr: ChangeRequest) -> bool:
    """Check whether the release gate is satisfied for a ChangeRequest.

    Gate conditions (all must be true):
      1. At least one QAReview for this CCR has verdict == 'pass'.
      2. There are >= 2 Approvals with decision == 'approve'.
      3. Of those approvals, >= 1 has role in {'instructor', 'instructor_lead'}.

    This function queries the DB directly — it does NOT rely on in-memory state.

    Args:
        session: Active AsyncSession.
        ccr:     The ChangeRequest to evaluate.

    Returns:
        True if all gate conditions are met, False otherwise.
    """
    # Check 1: at least one passing QA review
    qa_result = await session.execute(
        select(QAReview).where(
            QAReview.ccr_id == ccr.id,
            QAReview.verdict == "pass",
        )
    )
    passing_reviews = qa_result.scalars().all()
    if not passing_reviews:
        return False

    # Check 2: >= 2 approvals with decision == 'approve'
    approval_result = await session.execute(
        select(Approval).where(
            Approval.ccr_id == ccr.id,
            Approval.decision == "approve",
        )
    )
    approvals = approval_result.scalars().all()
    if len(approvals) < 2:
        return False

    # Check 3: >= 1 approval from an instructor role
    has_instructor = any(a.role in _INSTRUCTOR_ROLES for a in approvals)
    return has_instructor


# ---------------------------------------------------------------------------
# release_ccr
# ---------------------------------------------------------------------------


async def release_ccr(
    session: AsyncSession,
    *,
    ccr: ChangeRequest,
    previously_active: "Version | None",
    actor_role: str,
    actor_id: uuid.UUID | None,
) -> ChangeRequest:
    """Execute the release: activate the CCR's target version and mark the CCR approved.

    Steps:
      1. Check can_release; raise WorkflowError if False.
      2. Load the target version from ccr.target_version_id; raise WorkflowError
         if it's None or the version is not in 'approved' status.
      3. Call lifecycle.activate() to move target_version to 'active' and
         archive previously_active (if any).
      4. Persist each lifecycle HistoryEvent via history.persist_event().
      5. Set ccr.status = LifecycleStatus.approved.
      6. Flush and return the mutated CCR.

    Args:
        session:           Active AsyncSession.
        ccr:               The ChangeRequest being released.
        previously_active: The currently-active Version to archive, or None.
        actor_role:        Role of the actor performing the activation.
        actor_id:          UUID of the actor.

    Returns:
        The updated ChangeRequest with status == approved.

    Raises:
        WorkflowError:     if can_release() returns False, or CCR has no
                           approved target version.
        IllegalTransition: if target_version is not in 'approved' status.
        PermissionDenied:  if actor_role is not allowed for the transition.
    """
    from app.models.version import Version

    # Lock the CCR row to serialize concurrent release attempts
    await session.execute(
        select(ChangeRequest).where(ChangeRequest.id == ccr.id).with_for_update()
    )

    # Idempotency guard: clear domain error instead of confusing IllegalTransition
    if ccr.status == LifecycleStatus.approved:
        raise WorkflowError("CCR has already been released")

    if not await can_release(session, ccr):
        raise WorkflowError(
            "Release gate not satisfied: requires a passing QA review and "
            "at least two approvals including one instructor or instructor_lead."
        )

    # Load the target version from the CCR's stored reference
    if ccr.target_version_id is None:
        raise WorkflowError("CCR has no approved target version to release")

    version_result = await session.execute(
        select(Version).where(Version.id == ccr.target_version_id)
    )
    target_version = version_result.scalar_one_or_none()
    if target_version is None or target_version.status != LifecycleStatus.approved:
        raise WorkflowError("CCR has no approved target version to release")

    # Activate via the pure lifecycle helper (raises IllegalTransition / PermissionDenied)
    new_version, prev_version, lifecycle_events = activate(
        target_version, previously_active, actor_role, actor_id=actor_id
    )

    # Persist lifecycle events (A6 rule: one transition → one audit row via persist_event)
    for event in lifecycle_events:
        await persist_event(session, event)

    # Mark CCR as approved
    ccr.status = LifecycleStatus.approved
    session.add(ccr)
    await session.flush()

    return ccr


# ---------------------------------------------------------------------------
# activate_initial_release  (slice 5 — mandatory QA -> release)
# ---------------------------------------------------------------------------


async def activate_initial_release(
    session: AsyncSession,
    *,
    ccr: ChangeRequest,
    actor_id: uuid.UUID | None,
):
    """Activate the pre-active candidate version behind an initial-release CCR.

    This is the activation gate for a first-time authored course. Unlike
    :func:`release_ccr` (which activates a legacy ``Version`` from a delta) and
    the ``/merge`` path (which forks a new ``CurriculumVersion`` from an existing
    active parent), an initial release has NO parent to fork against — the full
    ``CurriculumVersion`` was already assembled by ``publish_draft`` in the
    ``review`` (candidate) state. So this simply **activates** that candidate,
    but ONLY after the SAME release gate the rest of the engine enforces.

    Steps:
      1. Lock the CCR row (serialize concurrent activations) + idempotency guard.
      2. Verify it is an initial-release CCR (carries the impact marker).
      3. Check ``can_release`` — a passing QA review + >= 2 approvals including an
         instructor. Raise WorkflowError (router → 4xx) with the gate reason if not.
      4. Flip the candidate ``CurriculumVersion`` -> active and set the
         curriculum's ``active_content_version_id``.
      5. Mark the CCR active; record a ``curriculum_released`` event.

    Returns the now-active ``CurriculumVersion``.

    Raises:
        WorkflowError: not an initial-release CCR, already activated, the gate is
            unmet, or the candidate/curriculum row is missing.
    """
    from app.builder.compile import initial_release_marker
    from app.models.content_model import CurriculumVersion
    from app.models.curriculum import Curriculum

    # Lock the CCR row to serialize concurrent activation attempts.
    await session.execute(
        select(ChangeRequest).where(ChangeRequest.id == ccr.id).with_for_update()
    )

    # Idempotency: a released initial-release CCR is terminal.
    if ccr.status in (LifecycleStatus.active, LifecycleStatus.approved):
        raise WorkflowError("this initial release has already been activated")

    marker = initial_release_marker(ccr)
    if marker is None or not marker.get("candidate_version_id"):
        raise WorkflowError("this change request is not an initial release")

    # The mandatory gate — identical to every other release.
    if not await can_release(session, ccr):
        raise WorkflowError(
            "Release gate not satisfied: requires a passing QA review and "
            "at least two approvals including one instructor or instructor_lead."
        )

    candidate_id = uuid.UUID(marker["candidate_version_id"])
    candidate = await session.get(CurriculumVersion, candidate_id)
    if candidate is None:
        raise WorkflowError("initial-release candidate version not found")
    if candidate.status == LifecycleStatus.active:
        raise WorkflowError("this initial release has already been activated")

    curriculum = await session.get(Curriculum, candidate.curriculum_id)
    if curriculum is None:
        raise WorkflowError("curriculum not found for the candidate version")

    # Activate: flip the candidate live + point the curriculum at it.
    candidate.status = LifecycleStatus.active
    curriculum.active_content_version_id = candidate.id
    ccr.status = LifecycleStatus.active
    session.add_all([candidate, curriculum, ccr])
    await session.flush()

    await record(
        session,
        actor_id=actor_id,
        event_type=EventType.curriculum_released,
        target=f"curriculum:{curriculum.id}",
        details={
            "ccr_id": str(ccr.id),
            "version_id": str(candidate.id),
            "semver": f"{candidate.major}.{candidate.minor}.{candidate.patch}",
            "initial_release": True,
        },
    )

    return candidate
