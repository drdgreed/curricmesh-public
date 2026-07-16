"""Router: POST /api/v1/curricula/{curriculum_id}/releases — executable release.

This is Phase C: a *release* takes a structured change-set and applies it to the
curriculum's currently-active manifest via :func:`app.core.fork.fork`, producing
and activating a NEW immutable ``CurriculumVersion`` (content-addressed, with
structural sharing — only the changed/added content is written). It is the
"merge the PR" operation; the rich-CCR-authoring UI (Feature B) collects the
change-set and POSTs it here when a change request is approved.

``fork()`` runs inside a SAVEPOINT and never commits — this router owns the
transaction boundary (commit on success). Domain failures map to HTTP:
``ForkValidationError`` → 422 (the proposed release is invalid: cycle, dangling
edge, bad placement); ``ConcurrentForkError`` → 409 (another release won the race
— re-read the active version and retry).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.core.fork import (
    ConcurrentForkError,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    ForkError,
    ForkValidationError,
    NewAsset,
    fork,
)
from app.core.history import EventType, record
from app.core.manifest import version_edges, version_members
from app.database import get_db
from app.models.curriculum import Curriculum
from app.schemas.release import (
    ReleaseChangeSet,
    ReleaseOut,
    ReleaseRequest,
    ReleaseSummary,
)

router = APIRouter(prefix="/api/v1/curricula", tags=["release"])

# Releasing activates a new curriculum version — high authority, same tier as
# version lifecycle ownership.
_RELEASE_ROLES = require_roles("architect", "program_manager")


def _to_fork_changes(body: ReleaseChangeSet) -> ForkChanges:
    """Map the HTTP change-set onto the ``fork()`` change-set dataclasses.

    Accepts any ``ReleaseChangeSet`` (including its ``ReleaseRequest`` subclass),
    so both the direct-release endpoint and the PR-style merge endpoint share one
    mapper.
    """
    return ForkChanges(
        changed={
            c.lineage_key: ContentEdit(
                content=c.content,
                metadata=c.metadata,
                section=c.section,
                week_index=c.week_index,
                order=c.order,
            )
            for c in body.changed
        },
        added=[
            NewAsset(
                lineage_key=a.lineage_key,
                kind=a.kind,
                content=a.content,
                metadata=a.metadata,
                section=a.section,
                week_index=a.week_index,
                order=a.order,
                source_url=a.source_url,
            )
            for a in body.added
        ],
        removed=set(body.removed),
        edges_added=[
            EdgeSpec(
                from_key=e.from_key,
                to_key=e.to_key,
                edge_type=e.edge_type,
                validated_against_seq=e.validated_against_seq,
            )
            for e in body.edges_added
        ],
        edges_removed=[
            EdgeSpec(
                from_key=e.from_key,
                to_key=e.to_key,
                edge_type=e.edge_type,
                validated_against_seq=e.validated_against_seq,
            )
            for e in body.edges_removed
        ],
    )


@router.post(
    "/{curriculum_id}/releases",
    response_model=ReleaseOut,
    status_code=201,
)
async def create_release(
    curriculum_id: uuid.UUID,
    body: ReleaseRequest,
    current: dict[str, Any] = Depends(_RELEASE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> ReleaseOut:
    """Apply a change-set as a new released CurriculumVersion (fork + activate)."""
    curriculum = (
        await db.execute(select(Curriculum).where(Curriculum.id == curriculum_id))
    ).scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    changes = _to_fork_changes(body)

    try:
        new_version = await fork(
            db,
            curriculum_id,
            bump=body.bump,
            changes=changes,
            expected_active_id=body.expected_active_id,
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

    actor_id: uuid.UUID | None = None
    try:
        actor_id = uuid.UUID(current["sub"])
    except (ValueError, KeyError):
        pass

    await record(
        db,
        actor_id=actor_id,
        event_type=EventType.curriculum_released,
        target=f"curriculum:{curriculum_id}",
        details={
            "version_id": str(new_version.id),
            "semver": semver,
            "ccr_id": str(body.ccr_id) if body.ccr_id else None,
            "note": body.note,
            "changed": len(body.changed),
            "added": len(body.added),
            "removed": len(body.removed),
        },
    )

    await db.commit()

    return ReleaseOut(
        curriculum_id=curriculum_id,
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
            changed=len(body.changed),
            added=len(body.added),
            removed=len(body.removed),
            edges_added=len(body.edges_added),
            edges_removed=len(body.edges_removed),
        ),
    )
