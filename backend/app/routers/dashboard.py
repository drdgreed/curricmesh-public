"""Router: /api/v1/dashboard — rollup view of all curricula and recent events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.auth.rbac import get_current_user
from app.core.cascade.engine import alignment_report_for_version
from app.core.manifest import (
    active_curriculum_version,
    version_edges,
    version_members,
)
from app.core.naming import asset_display_names
from app.database import get_db
from app.models.cohort import Cohort
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent
from app.models.structure import Asset, AssetVersion
from app.models.user import User
from app.models.version import Version
from app.models.workflow import ChangeRequest
from app.schemas.workflow import (
    DashboardCohortSummary,
    DashboardCurriculumEntry,
    DashboardHistoryEntry,
    DashboardOut,
    DashboardVersionSummary,
    MisalignmentEntry,
)

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


async def _latest_asset_version_timestamps(
    db: AsyncSession, asset_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Batched: map each asset_id → its latest AssetVersion.created_at.

    Mirrors the graph router's latest-AssetVersion logic (max(created_at) per
    asset). One query regardless of how many asset ids — no N+1.
    """
    if not asset_ids:
        return {}
    result = await db.execute(
        select(AssetVersion.asset_id, func.max(AssetVersion.created_at))
        .where(AssetVersion.asset_id.in_(asset_ids))
        .group_by(AssetVersion.asset_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def _manifest_alignment_entries(
    db: AsyncSession, cversion_id: uuid.UUID
) -> list[MisalignmentEntry]:
    """Build the dashboard alignment entries for a curriculum from its **manifest**.

    The strangler read path (M2.2): when a curriculum has a populated manifest
    (``active_curriculum_version`` resolved), its misaligned (dependent,
    prerequisite) pairs are read from ``manifest_alignment`` + ``version_edges``
    instead of the legacy ``alignment_report_for_version``.

    Each entry mirrors the legacy schema exactly, so the dashboard contract is
    byte-for-byte unchanged and the committed golden fixtures still match:

      * ``dependent_asset_id`` / ``dependency_asset_id`` — the **legacy** ``Asset``
        ids, mapped from the member's ``lineage_key`` (== legacy ``Asset.key``).
        Emitting legacy ids keeps the id space identical to the legacy path during
        the strangler (the new ``LineageAsset`` ids become canonical at M3).
      * ``dependent_updated_at`` / ``dependency_updated_at`` — the selected
        ``ContentVersion.created_at`` carried on each member (== legacy
        ``max(AssetVersion.created_at)`` the back-fill selected).
      * ``dependent_asset_name`` / ``dependency_asset_name`` — resolved through the
        shared ``asset_display_names`` (the same resolver the graph port uses), so
        the friendly ``"{container} · {kind}"`` label — including the ``Week N:``
        prefix and the Project *title* — matches the golden. (The manifest's bare
        ``section`` is lossy here: it drops the week prefix and collapses every
        project asset to "Projects", so it cannot reproduce the committed names on
        its own; ``asset_display_names`` is the source of truth for labels.)

    The (dependent, prerequisite) pairs come from the version's edges with the
    **per-edge** timestamp rule, exactly mirroring the legacy ``alignment_report``:
    for each edge ``from -> to`` (``from`` = prerequisite, ``to`` = dependent), we
    emit one entry *only when the dependent's selected content predates that
    specific prerequisite's*. (``manifest_alignment`` collapses staleness to the
    set of stale dependents and so loses *which* prerequisite each is behind — a
    dependent can have several incoming edges, only some of them stale — so the
    pairs are recomputed per edge here rather than read off that set.)
    """
    members = await version_members(db, cversion_id)
    edges = await version_edges(db, cversion_id)

    member_by_lineage_id = {m.asset_id: m for m in members}

    # lineage_key -> legacy Asset.id, so names resolve through the SAME resolver
    # the legacy path uses and the emitted ids stay in the legacy id space.
    lineage_keys = {m.lineage_key for m in members}
    legacy_id_by_key: dict[str, uuid.UUID] = {}
    if lineage_keys:
        rows = await db.execute(
            select(Asset.id, Asset.key).where(Asset.key.in_(lineage_keys))
        )
        legacy_id_by_key = {key: aid for aid, key in rows.all()}

    legacy_ids = set(legacy_id_by_key.values())
    names = await asset_display_names(db, legacy_ids)

    entries: list[MisalignmentEntry] = []
    for e in edges:
        # from = prerequisite (dependency), to = dependent.
        dependent_member = member_by_lineage_id.get(e.to_asset_id)
        dependency_member = member_by_lineage_id.get(e.from_asset_id)
        if dependent_member is None or dependency_member is None:
            continue
        # Per-edge staleness (the legacy timestamp rule): the dependent is stale
        # w.r.t. THIS prerequisite only when its selected content predates it.
        if not (
            dependent_member.content_created_at
            < dependency_member.content_created_at
        ):
            continue

        dependent_legacy_id = legacy_id_by_key.get(dependent_member.lineage_key)
        dependency_legacy_id = legacy_id_by_key.get(dependency_member.lineage_key)
        # Fall back to the lineage id if the legacy row is gone (defensive — the
        # strangler keeps both in lockstep, so this should not happen on the seed).
        dependent_id = dependent_legacy_id or e.to_asset_id
        dependency_id = dependency_legacy_id or e.from_asset_id

        entries.append(
            MisalignmentEntry(
                dependent_asset_id=dependent_id,
                dependency_asset_id=dependency_id,
                reason=(
                    f"Dependent asset {dependent_id} was last updated "
                    f"{dependent_member.content_created_at.isoformat()} which "
                    f"predates its dependency {dependency_id} last updated "
                    f"{dependency_member.content_created_at.isoformat()}."
                ),
                dependent_asset_name=names.get(
                    dependent_id, str(dependent_id)
                ),
                dependency_asset_name=names.get(
                    dependency_id, str(dependency_id)
                ),
                dependent_updated_at=dependent_member.content_created_at,
                dependency_updated_at=dependency_member.content_created_at,
            )
        )
    return entries


def _parse_target(target: str | None) -> tuple[str, uuid.UUID] | None:
    """Parse a HistoryEvent.target into ``(kind, id)``.

    Handles BOTH the seed's prefixed form (``"version:<uuid>"``,
    ``"curriculum:<uuid>"``, ``"ccr:<uuid>"``) and the runtime engine's bare-UUID
    form (``"<uuid>"`` for version-lifecycle events). Returns ``("", id)`` for a
    bare UUID (the caller infers the kind from ``event_type``), or ``None`` if it
    can't be parsed.
    """
    if not target:
        return None
    if ":" in target:
        prefix, _, rest = target.partition(":")
        try:
            return prefix, uuid.UUID(rest)
        except ValueError:
            return None
    try:
        return "", uuid.UUID(target)
    except ValueError:
        return None


def _resolve_target_label(
    event: HistoryEvent,
    version_semvers: dict[str, str],
    curriculum_names: dict[str, str],
    ccr_titles: dict[str, str],
    asset_names: dict[uuid.UUID, str],
) -> str | None:
    """Resolve a HistoryEvent.target to a human label using prefetched lookups.

    Targets come in two shapes: the seed emits ``"<kind>:<uuid>"`` (version /
    curriculum / ccr); the runtime engine emits a bare ``"<uuid>"`` for version
    lifecycle events and ``"ccr:<uuid>"`` for CCR events. Both are handled.
    """
    target = event.target
    if target is None:
        return None
    parsed = _parse_target(target)
    if parsed is None:
        return target
    kind, tid = parsed
    sid = str(tid)
    event_type = str(event.event_type)

    # On a lookup miss (referenced entity deleted / out of scope) return a clean
    # generic label — never the raw "<kind>:<uuid>" string (no-UUID guarantee).
    if kind == "version" or (kind == "" and event_type.startswith("version_")):
        return version_semvers.get(sid, "(unknown version)")
    if kind == "curriculum":
        return curriculum_names.get(sid, "(unknown curriculum)")
    if kind == "ccr":
        return ccr_titles.get(sid, "(unknown change request)")
    # bare UUID, non-version → an asset
    return asset_names.get(tid, "(unknown asset)")


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    """Return a rollup JSON: all curricula with versions, cohorts, and recent history.

    This is the REST equivalent of the framework's status.json.
    """
    # Fetch all curricula
    curricula_result = await db.execute(select(Curriculum).order_by(Curriculum.created_at))
    all_curricula = curricula_result.scalars().all()

    # Fetch all versions in one query, grouped by curriculum
    versions_result = await db.execute(select(Version).order_by(Version.created_at))
    all_versions = versions_result.scalars().all()
    versions_by_curriculum: dict = {}
    for v in all_versions:
        versions_by_curriculum.setdefault(str(v.curriculum_id), []).append(v)

    # Fetch all cohorts
    cohorts_result = await db.execute(select(Cohort).order_by(Cohort.created_at))
    all_cohorts = cohorts_result.scalars().all()
    cohorts_by_curriculum: dict = {}
    for c in all_cohorts:
        cohorts_by_curriculum.setdefault(str(c.curriculum_id), []).append(c)

    # Fetch 20 most recent history events
    events_result = await db.execute(
        select(HistoryEvent).order_by(HistoryEvent.created_at.desc()).limit(20)
    )
    recent_events = events_result.scalars().all()

    # ------------------------------------------------------------------
    # Alignment (M2.2 strangler): per curriculum, read misalignment entries from
    # the immutable MANIFEST when one is populated, else fall back to the legacy
    # alignment path. The fallback keeps old-model fixtures green.
    #
    # Manifest-path entries are built eagerly (each resolves its own names from
    # its members). Legacy-path entries defer to a single BATCHED name/timestamp
    # resolution (Pass 1 collects the ids, Pass 2 builds the entries) so the
    # legacy path stays an O(curricula)-query read with no N+1.
    # ------------------------------------------------------------------
    manifest_entries_by_curriculum: dict[str, list[MisalignmentEntry]] = {}
    raw_alignment_by_curriculum: dict[str, list] = {}
    alignment_asset_ids: set[uuid.UUID] = set()

    for curriculum in all_curricula:
        cid = str(curriculum.id)
        versions = versions_by_curriculum.get(cid, [])

        # Prefer the manifest when this curriculum has one back-filled.
        cversion = await active_curriculum_version(db, curriculum.id)
        if cversion is not None:
            manifest_entries_by_curriculum[cid] = await _manifest_alignment_entries(
                db, cversion.id
            )
            continue

        # --- legacy fallback ---
        active_version_id = curriculum.current_version_id
        if active_version_id is None:
            for v in versions:
                if v.status == LifecycleStatus.active:
                    active_version_id = v.id
                    break
        if active_version_id is None:
            raw_alignment_by_curriculum[cid] = []
            continue

        # NOTE: alignment_report_for_version runs once per curriculum
        # (O(curricula) queries total). Acceptable at demo scale; switch to a
        # batch or materialized approach when curriculum counts grow large.
        misalignments = await alignment_report_for_version(db, active_version_id)
        raw_alignment_by_curriculum[cid] = misalignments
        for m in misalignments:
            alignment_asset_ids.add(m.dependent_asset_id)
            alignment_asset_ids.add(m.dependency_asset_id)

    # Batched name + timestamp resolution across ALL legacy-path misalignments.
    alignment_names = await asset_display_names(db, alignment_asset_ids)
    alignment_timestamps = await _latest_asset_version_timestamps(
        db, alignment_asset_ids
    )

    curriculum_entries = []
    for curriculum in all_curricula:
        cid = str(curriculum.id)
        versions = versions_by_curriculum.get(cid, [])
        cohorts = cohorts_by_curriculum.get(cid, [])

        if cid in manifest_entries_by_curriculum:
            alignment_entries = manifest_entries_by_curriculum[cid]
        else:
            alignment_entries = [
                MisalignmentEntry(
                    dependent_asset_id=m.dependent_asset_id,
                    dependency_asset_id=m.dependency_asset_id,
                    reason=m.reason,
                    dependent_asset_name=alignment_names.get(
                        m.dependent_asset_id, str(m.dependent_asset_id)
                    ),
                    dependency_asset_name=alignment_names.get(
                        m.dependency_asset_id, str(m.dependency_asset_id)
                    ),
                    dependent_updated_at=alignment_timestamps.get(
                        m.dependent_asset_id
                    ),
                    dependency_updated_at=alignment_timestamps.get(
                        m.dependency_asset_id
                    ),
                )
                for m in raw_alignment_by_curriculum.get(cid, [])
            ]

        curriculum_entries.append(
            DashboardCurriculumEntry(
                id=curriculum.id,
                name=curriculum.name,
                slug=curriculum.slug,
                current_version_id=curriculum.current_version_id,
                versions=[
                    DashboardVersionSummary(
                        id=v.id,
                        semver=f"{v.major}.{v.minor}.{v.patch}",
                        status=v.status,
                        created_at=v.created_at,
                    )
                    for v in versions
                ],
                cohorts=[
                    DashboardCohortSummary(
                        id=c.id,
                        name=c.name,
                        version_id=c.version_id,
                        start_date=c.start_date,
                        end_date=c.end_date,
                    )
                    for c in cohorts
                ],
                alignment=alignment_entries,
            )
        )

    # ------------------------------------------------------------------
    # Recent-events actor + target resolution (all batched — no N+1).
    # ------------------------------------------------------------------
    # Collect actor ids.
    actor_ids = {e.actor_id for e in recent_events if e.actor_id is not None}
    actor_labels: dict[uuid.UUID, str] = {}
    if actor_ids:
        user_result = await db.execute(select(User).where(User.id.in_(actor_ids)))
        for u in user_result.scalars().all():
            actor_labels[u.id] = u.display_name or u.email

    # Collect target references, handling BOTH "<kind>:<uuid>" (seed) and bare
    # "<uuid>" (runtime) forms. version/curriculum labels come from data already
    # loaded above, so only ccr titles + asset names need a query.
    ccr_target_ids: set[uuid.UUID] = set()
    asset_target_ids: set[uuid.UUID] = set()
    for e in recent_events:
        parsed = _parse_target(e.target)
        if parsed is None:
            continue
        kind, tid = parsed
        if kind == "ccr":
            ccr_target_ids.add(tid)
        elif kind in ("version", "curriculum"):
            pass  # resolved from all_versions / all_curricula (no extra query)
        elif kind == "" and not str(e.event_type).startswith("version_"):
            asset_target_ids.add(tid)

    version_semvers: dict[str, str] = {
        str(v.id): f"v{v.major}.{v.minor}.{v.patch}" for v in all_versions
    }
    curriculum_names: dict[str, str] = {str(c.id): c.name for c in all_curricula}

    ccr_titles: dict[str, str] = {}
    if ccr_target_ids:
        cresult = await db.execute(
            select(ChangeRequest).where(ChangeRequest.id.in_(ccr_target_ids))
        )
        for c in cresult.scalars().all():
            ccr_titles[str(c.id)] = c.title

    target_asset_names = await asset_display_names(db, asset_target_ids)

    history_entries = []
    for e in recent_events:
        # actor_label: User display name → details.actor_role → "AI Researcher"/"System".
        if e.actor_id is not None and e.actor_id in actor_labels:
            actor_label = actor_labels[e.actor_id]
        elif e.details and e.details.get("actor_role"):
            actor_label = e.details["actor_role"]
        elif e.actor_id is not None:
            # Actor id present but user row gone (SET NULL race / deleted) → AI/system fallback.
            actor_label = "AI Researcher"
        else:
            actor_label = "System"

        target_label = _resolve_target_label(
            e, version_semvers, curriculum_names, ccr_titles, target_asset_names
        )

        history_entries.append(
            DashboardHistoryEntry(
                id=e.id,
                event_type=e.event_type,
                target=e.target,
                actor_id=e.actor_id,
                details=e.details,
                created_at=e.created_at,
                actor_label=actor_label,
                target_label=target_label,
            )
        )

    return DashboardOut(curricula=curriculum_entries, recent_events=history_entries)
