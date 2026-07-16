"""Router: external sync adapters (V3-C).

Thin endpoints over the ``SyncProvider`` seam:

  - ``POST /api/v1/curricula/{id}/sync?target=github|lms`` — resolve the
    curriculum's active/latest released version, build a ``VersionManifest``,
    call the provider, **persist a ``SyncLog``** with the outcome, and return it.
  - ``GET /api/v1/curricula/{id}/sync-log`` — the curriculum's sync history.

Role-gated to ``devops`` / ``architect`` (publishing to external systems is an
ops/architecture concern). Tenant isolation comes from the app-layer auto-filter
(and Postgres RLS under a least-privilege role) — the endpoints never filter on
``organization_id`` themselves.

Failure contract: a provider that raises is recorded as a ``failed`` ``SyncLog``
AND surfaced as a 502 — a failed sync is NEVER silently returned as a success.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.structure import Module
from app.models.sync import SyncLog
from app.models.version import Version
from app.schemas.sync import SyncLogOut
from app.sync.base import SyncProvider, VersionManifest
from app.sync.providers import get_sync_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/curricula", tags=["sync"])

_SYNC_ROLES = require_roles("devops", "architect")


def resolve_sync_provider(
    target: str = Query(..., description="External target: github | lms"),
) -> SyncProvider:
    """Dependency wrapper around the provider factory (400 on unknown target).

    Exposed as a dependency so tests can override it with a failing fake to prove
    the failure path records a ``failed`` log and surfaces the error.
    """
    try:
        return get_sync_provider(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _resolve_version(db: AsyncSession, curriculum: Curriculum) -> Version | None:
    """Resolve the version to publish: the current version, else the latest
    *active* version. A draft is never published — the fallback filters on
    ``status == active`` (mirrors dashboard.py); if none resolves the caller
    returns a clean 400."""
    if curriculum.current_version_id is not None:
        result = await db.execute(
            select(Version).where(Version.id == curriculum.current_version_id)
        )
        version = result.scalar_one_or_none()
        if version is not None:
            return version

    result = await db.execute(
        select(Version)
        .where(
            Version.curriculum_id == curriculum.id,
            Version.status == LifecycleStatus.active,
        )
        .order_by(Version.major.desc(), Version.minor.desc(), Version.patch.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_manifest(
    db: AsyncSession, curriculum: Curriculum, version: Version
) -> VersionManifest:
    """Assemble the pure-data manifest from the resolved version's modules."""
    result = await db.execute(
        select(Module).where(Module.version_id == version.id).order_by(Module.index)
    )
    modules = [m.focus or f"Module {m.index}" for m in result.scalars().all()]
    return VersionManifest(
        curriculum_id=curriculum.id,
        curriculum_name=curriculum.name,
        version=f"v{version.major}.{version.minor}.{version.patch}",
        modules=modules,
        released_at=version.created_at.isoformat(),
    )


@router.post("/{curriculum_id}/sync", response_model=SyncLogOut)
async def sync_curriculum(
    curriculum_id: uuid.UUID,
    target: str = Query(..., description="External target: github | lms"),
    current: dict[str, Any] = Depends(_SYNC_ROLES),
    db: AsyncSession = Depends(get_db),
    provider: SyncProvider = Depends(resolve_sync_provider),
) -> SyncLogOut:
    """Publish the curriculum's released version to ``target`` and log the attempt.

    The provider call's outcome — success OR failure — is always persisted as a
    ``SyncLog``. A failing provider is recorded as ``failed`` and surfaced as a
    502; it is never silently returned as a 200 success.
    """
    cur_result = await db.execute(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    curriculum = cur_result.scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    version = await _resolve_version(db, curriculum)
    if version is None:
        raise HTTPException(status_code=400, detail="Curriculum has no version to sync")

    manifest = await _build_manifest(db, curriculum, version)

    try:
        result = await provider.publish(manifest)
    except Exception as exc:  # noqa: BLE001 — surface AND log, never swallow.
        # Record the failed attempt before surfacing the error.
        log = SyncLog(
            curriculum_id=curriculum.id,
            version_id=version.id,
            target=target,
            status="failed",
            detail={"url": None, "message": str(exc)},
        )
        db.add(log)
        await db.commit()
        logger.warning("Sync to %r failed for curriculum %s: %s", target, curriculum.id, exc)
        raise HTTPException(
            status_code=502, detail=f"Sync to '{target}' failed: {exc}"
        ) from exc

    log = SyncLog(
        curriculum_id=curriculum.id,
        version_id=version.id,
        target=result.target,
        status=result.status,
        detail={"url": result.url, "message": result.message},
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return SyncLogOut.model_validate(log)


@router.get("/{curriculum_id}/sync-log", response_model=list[SyncLogOut])
async def get_sync_log(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(_SYNC_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[SyncLogOut]:
    """Return the curriculum's sync history (org-scoped, newest first)."""
    cur_result = await db.execute(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    if cur_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    result = await db.execute(
        select(SyncLog)
        .where(SyncLog.curriculum_id == curriculum_id)
        .order_by(SyncLog.created_at.desc())
    )
    return [SyncLogOut.model_validate(r) for r in result.scalars().all()]
