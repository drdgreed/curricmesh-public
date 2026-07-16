"""Router: /api/v1/ccrs/{id}/approvals and /api/v1/ccrs/{id}/release."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user, require_roles
from app.builder.compile import initial_release_marker
from app.config import settings
from app.core.fork import (
    ConcurrentForkError,
    ForkError,
    ForkValidationError,
    fork,
)
from app.core.history import EventType, record
from app.core.manifest import version_edges, version_members
from app.core.retrieval.ingest_runner import SessionScope, run_ingest
from app.core.workflow.engine import (
    activate_initial_release,
    can_release,
    record_approval,
    release_ccr,
)
from app.database import get_db, org_scoped_session
from app.freshness_pipeline.syncing import sync_release
from app.integrations.notifier import notify
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.sync import SyncTarget
from app.models.version import Version
from app.models.workflow import Approval, ChangeRequest, QAReview
from app.routers.releases import _to_fork_changes
from app.schemas.release import (
    ReleaseChangeSet,
    ReleaseGateOut,
    ReleaseOut,
    ReleaseSummary,
)
from app.tenant import require_org

logger = logging.getLogger(__name__)

_INSTRUCTOR_APPROVAL_ROLES = {"instructor", "instructor_lead"}
from app.schemas.workflow import ApprovalCreate, ApprovalOut, CCROut

router = APIRouter(prefix="/api/v1/ccrs", tags=["approvals"])

_APPROVE_ROLES = require_roles("instructor", "instructor_lead", "architect", "program_manager")
_RELEASE_ROLES = require_roles("program_manager", "architect")


def get_ingest_session_scope() -> SessionScope:
    """The session factory the background release-ingest runner opens.

    Production: ``org_scoped_session`` — the runner runs OUTSIDE the request
    (embedding a whole course is slow; the release must not block on it), so it
    must set the tenant ContextVar + ``app.current_org`` GUC on its own session.
    Tests override this to yield an already-org-scoped session so no real
    app-engine connection is opened, yet cross-tenant isolation is still
    exercised. Mirrors ``authoring_ai.get_generation_session_scope``.
    """
    return org_scoped_session


def _schedule_ingest(
    background_tasks: BackgroundTasks | None,
    session_scope: SessionScope,
    version_id: uuid.UUID,
) -> None:
    """Schedule a background retrieval-index build for a just-released version.

    Captures the tenant from the live request context (``require_org``) and hands
    it to the runner, which opens its OWN org-scoped session. A no-op when
    ``background_tasks`` is absent (e.g. handler called directly in a unit test
    that isn't exercising the hook).
    """
    if background_tasks is None:
        return
    org_id = require_org()
    background_tasks.add_task(
        run_ingest,
        version_id,
        org_id,
        session_scope=session_scope,
    )


@router.post("/{ccr_id}/approvals", response_model=ApprovalOut, status_code=201)
async def submit_approval(
    ccr_id: uuid.UUID,
    body: ApprovalCreate,
    current: dict[str, Any] = Depends(_APPROVE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    """Submit an approval decision. WorkflowError → 400 via central handler."""
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
    ccr = result.scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    approver_id: uuid.UUID | None = None
    try:
        approver_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    approval = await record_approval(
        db,
        ccr=ccr,
        approver_id=approver_id,
        role=current["role"],
        decision=body.decision,
    )
    await db.commit()
    await db.refresh(approval)
    return ApprovalOut.model_validate(approval)


@router.get("/{ccr_id}/gate", response_model=ReleaseGateOut)
async def release_gate(
    ccr_id: uuid.UUID,
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReleaseGateOut:
    """Report the release-gate status for a CCR (what the merge gate still needs)."""
    ccr = (
        await db.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
    ).scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    qa_passed = (
        await db.execute(
            select(QAReview.id).where(
                QAReview.ccr_id == ccr_id, QAReview.verdict == "pass"
            )
        )
    ).first() is not None

    approvals = (
        (
            await db.execute(
                select(Approval).where(
                    Approval.ccr_id == ccr_id, Approval.decision == "approve"
                )
            )
        )
        .scalars()
        .all()
    )

    return ReleaseGateOut(
        has_change_set=ccr.change_set is not None,
        qa_passed=qa_passed,
        approval_count=len(approvals),
        has_instructor_approval=any(
            a.role in _INSTRUCTOR_APPROVAL_ROLES for a in approvals
        ),
        can_release=await can_release(db, ccr),
    )


@router.post("/{ccr_id}/release", response_model=CCROut)
async def release(
    ccr_id: uuid.UUID,
    background_tasks: BackgroundTasks = None,
    current: dict[str, Any] = Depends(_RELEASE_ROLES),
    db: AsyncSession = Depends(get_db),
    session_scope: SessionScope = Depends(get_ingest_session_scope),
) -> CCROut:
    """Execute the CCR release gate. WorkflowError → 400 via central handler."""
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
    ccr = result.scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    actor_id: uuid.UUID | None = None
    try:
        actor_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    # Initial-release branch (slice 5): a first-time authored course was published
    # as a pre-active candidate CurriculumVersion + this CCR. There is no parent to
    # fork against and no legacy Version to activate — the candidate is already
    # assembled, so activate it once the QA + approval gate clears. WorkflowError
    # (e.g. gate unmet) → 400 via the central handler.
    if initial_release_marker(ccr) is not None:
        activated = await activate_initial_release(db, ccr=ccr, actor_id=actor_id)
        await db.commit()
        await db.refresh(ccr)
        # A CurriculumVersion just went active → (re)build its retrieval index in
        # the background AFTER the release commit, so the RAG tutor can answer
        # about it. Slow (embeds the whole course) → must not block the release.
        _schedule_ingest(background_tasks, session_scope, activated.id)
        await notify(
            EventType.version_activated,
            {
                "ccr_id": str(ccr.id),
                "semver": f"{activated.major}.{activated.minor}.{activated.patch}",
                "curriculum_id": str(ccr.curriculum_id),
            },
        )
        return CCROut.model_validate(ccr)

    # Validate that the CCR has a target version set
    if ccr.target_version_id is None:
        raise HTTPException(
            status_code=400,
            detail="No approved version found for this curriculum to release",
        )

    # Find the currently active version (to archive on release), if any.
    active_result = await db.execute(
        select(Version)
        .where(
            Version.curriculum_id == ccr.curriculum_id,
            Version.status == LifecycleStatus.active,
        )
    )
    previously_active = active_result.scalars().first()

    updated_ccr = await release_ccr(
        db,
        ccr=ccr,
        previously_active=previously_active,
        actor_role=current["role"],
        actor_id=actor_id,
    )

    # Update the curriculum's current_version_id pointer to reflect the newly-active version.
    cur_result = await db.execute(
        select(Curriculum).where(Curriculum.id == ccr.curriculum_id)
    )
    curriculum = cur_result.scalar_one_or_none()
    if curriculum is not None:
        curriculum.current_version_id = ccr.target_version_id
        db.add(curriculum)

    await db.commit()
    await db.refresh(updated_ccr)

    # Notify AFTER commit — notification failure must NOT roll back the transaction.
    # Load the activated version to get its semver string for the notification.
    semver = "<unknown>"
    if updated_ccr.target_version_id is not None:
        ver_result = await db.execute(
            select(Version).where(Version.id == updated_ccr.target_version_id)
        )
        activated_version = ver_result.scalar_one_or_none()
        if activated_version is not None:
            semver = f"{activated_version.major}.{activated_version.minor}.{activated_version.patch}"

    await notify(
        EventType.version_activated,
        {
            "ccr_id": str(updated_ccr.id),
            "semver": semver,
            "curriculum_id": str(updated_ccr.curriculum_id),
        },
    )

    return CCROut.model_validate(updated_ccr)


@router.post("/{ccr_id}/merge", response_model=ReleaseOut)
async def merge(
    ccr_id: uuid.UUID,
    background_tasks: BackgroundTasks = None,
    current: dict[str, Any] = Depends(_RELEASE_ROLES),
    db: AsyncSession = Depends(get_db),
    session_scope: SessionScope = Depends(get_ingest_session_scope),
) -> ReleaseOut:
    """Merge an approved CCR: replay its executable change-set through fork().

    PR-style "merge the PR": once a change request carrying a structured
    change-set has passed the approval gate (:func:`can_release`), this applies
    that change-set to the curriculum's currently-active manifest via
    :func:`app.core.fork.fork`, producing + activating a new immutable
    ``CurriculumVersion``. ``fork()`` runs in a SAVEPOINT and never commits — this
    handler owns the transaction boundary.
    """
    # Lock the CCR row so two concurrent merges of the same CCR serialize; the
    # second then sees status == active below and is rejected (idempotency).
    result = await db.execute(
        select(ChangeRequest).where(ChangeRequest.id == ccr_id).with_for_update()
    )
    ccr = result.scalar_one_or_none()
    if ccr is None:
        raise HTTPException(status_code=404, detail="CCR not found")

    # Already merged (a prior merge set status to active) — don't fork again.
    if ccr.status == LifecycleStatus.active:
        raise HTTPException(
            status_code=409, detail="this change request has already been merged"
        )

    if ccr.change_set is None:
        raise HTTPException(
            status_code=400,
            detail="this change request has no executable change-set",
        )

    if not await can_release(db, ccr):
        raise HTTPException(
            status_code=409,
            detail="change request is not approved for release yet",
        )

    change_set = ReleaseChangeSet.model_validate(ccr.change_set)
    changes = _to_fork_changes(change_set)

    try:
        new_version = await fork(
            db,
            ccr.curriculum_id,
            bump=change_set.bump,
            changes=changes,
        )
    except ForkValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConcurrentForkError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ForkError as exc:  # any other fork failure (e.g. no active version)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    members = await version_members(db, new_version.id)
    edges = await version_edges(db, new_version.id)
    semver = f"{new_version.major}.{new_version.minor}.{new_version.patch}"

    # Mark the CCR merged/released. release_ccr() uses LifecycleStatus.approved as
    # its terminal "released" value; the spec asks for active. The merge here is the
    # release — the new CurriculumVersion is already active — so the CCR moves to
    # active to mirror that.
    ccr.status = LifecycleStatus.active
    db.add(ccr)

    actor_id: uuid.UUID | None = None
    try:
        actor_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    await record(
        db,
        actor_id=actor_id,
        event_type=EventType.curriculum_released,
        target=f"curriculum:{ccr.curriculum_id}",
        details={
            "ccr_id": str(ccr.id),
            "version_id": str(new_version.id),
            "semver": semver,
        },
    )

    await db.commit()

    # A new CurriculumVersion just went active → (re)build its retrieval index in
    # the background AFTER the release commit, so the RAG tutor can answer about
    # it. Slow (embeds the whole course) → must not block the merge response.
    _schedule_ingest(background_tasks, session_scope, new_version.id)

    # Post-merge sync hook (Phase 4, kill-switched).
    # Runs AFTER the release commit so a sync hiccup cannot poison the release
    # transaction. Wrapped in a single try/except so no sync problem can alter
    # the merge response — sync status lives in SyncLog / the runner digest.
    try:
        if settings.FRESHNESS_SYNC_ENABLED and settings.SYNC_GITHUB_TOKEN:
            cur_result = await db.execute(
                select(Curriculum).where(Curriculum.id == ccr.curriculum_id)
            )
            curriculum = cur_result.scalar_one_or_none()
            if curriculum is not None:
                targets_result = await db.execute(
                    select(SyncTarget).where(
                        SyncTarget.curriculum_id == ccr.curriculum_id,
                        SyncTarget.active == True,  # noqa: E712
                    )
                )
                for target in targets_result.scalars().all():
                    await sync_release(
                        db,
                        curriculum=curriculum,
                        new_version=new_version,
                        target=target,
                        ccr=ccr,
                    )
                await db.commit()
    except Exception:
        logger.warning(
            "Post-merge sync failed for curriculum %s",
            ccr.curriculum_id,
            exc_info=True,
        )

    return ReleaseOut(
        curriculum_id=ccr.curriculum_id,
        version_id=new_version.id,
        semver=semver,
        status=str(
            new_version.status.value
            if hasattr(new_version.status, "value")
            else new_version.status
        ),
        parent_version_id=new_version.parent_version_id,
        member_count=len(members),
        edge_count=len(edges),
        summary=ReleaseSummary(
            changed=len(change_set.changed),
            added=len(change_set.added),
            removed=len(change_set.removed),
            edges_added=len(change_set.edges_added),
            edges_removed=len(change_set.edges_removed),
        ),
    )
