"""V3-A analytics engine — change velocity & time-in-state.

Pure async functions over an ``AsyncSession``. NO FastAPI imports: the router
(app/routers/analytics.py) is the thin HTTP layer; this module is the read-only
aggregation engine, mirroring the engine-pure / routers-thin convention used
elsewhere in the codebase.

All reads go through the scoped session. For full mapped-entity loads the
app-layer tenant auto-filter (``with_loader_criteria``) + Postgres RLS keep the
query org-scoped automatically. Column-only selects (e.g. ``select(Model.col)``)
ESCAPE the auto-filter — it only applies to entity loads — so every such query
on a TenantScoped model carries an EXPLICIT ``organization_id == require_org()``
guard here. This keeps the engine correct independent of DB role (the demo
superuser bypasses RLS, P-001) and FastAPI-free (``app.tenant`` is stdlib-only).

Data sources (no migration — pure read over existing tables):
  - change_requests : status, created_at, curriculum_id
  - versions        : status, major/minor/patch, created_at, curriculum_id
  - history_events  : the transition log. Time-in-state and release timing are
                      derived from these. The emitted event_type strings come
                      from app/core/history.py (EventType) and
                      app/core/versioning/lifecycle.transition():
                        version_<status> : a version lifecycle transition.
                            target = str(version.id) (raw UUID, no prefix);
                            details = {from_status, to_status, actor_role}.
                        ccr_created      : a CCR was opened (target "ccr:<id>").
                        qa_passed        : a CCR passed QA (target "ccr:<id>").

## Time-in-state honesty
Version lifecycle transitions are the only per-state transition log we have:
each ``version_<to>`` event carries the from/to status and a timestamp, so the
dwell time in ``from_status`` is (this event's time − the prior event's time),
with the version's ``created_at`` anchoring the first ``draft`` interval. CCRs
do NOT emit a history row per status change (only ``ccr_created`` and
``qa_passed``; the release jump draft→approved is unlogged), so CCR dwell time
is NOT reconstructable. We therefore key time-in-state off VERSION transitions
and report any LifecycleStatus with no reconstructable interval as ``n=0``
rather than fabricating a duration.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent
from app.models.version import Version
from app.models.workflow import ChangeRequest
from app.schemas.analytics import (
    AnalyticsOverview,
    CadenceSummary,
    StateCount,
    StateDuration,
    VelocityBucket,
)
from app.tenant import require_org

# The fine-grained version lifecycle events emitted by lifecycle.transition()
# ("version_<status>"). These are the transition log for time-in-state.
_VERSION_EVENT_PREFIX = "version_"
# The event marking a version going live — our "released" signal for cadence.
_VERSION_ACTIVE_EVENT = "version_active"

_SECONDS_PER_DAY = 86_400.0


def _as_utc(dt: datetime) -> datetime:
    """Normalize to an aware UTC datetime so subtraction is always safe."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bucket_start(dt: datetime, bucket: str) -> datetime:
    """Floor a timestamp to the start of its week (Monday) or month, UTC."""
    dt = _as_utc(dt)
    if bucket == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # default / "week": floor to Monday 00:00:00 UTC
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
# change_velocity
# ---------------------------------------------------------------------------


async def change_velocity(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID | None = None,
    bucket: str = "week",
) -> list[VelocityBucket]:
    """Counts of CCRs opened and versions released per time bucket.

    "CCRs opened" is counted by ``change_requests.created_at``. "Versions
    released" is counted by the timestamp of each version's ``version_active``
    history event (the moment it went live), restricted to versions in scope.

    Buckets are sorted ascending by ``bucket_start``; empty buckets between
    activity are not emitted (sparse series).
    """
    bucket = bucket if bucket in {"week", "month"} else "week"

    # CCRs opened. Column-only select escapes the entity auto-filter, so scope
    # it explicitly (correct under any DB role, including the demo superuser).
    ccr_stmt = select(ChangeRequest.created_at).where(
        ChangeRequest.organization_id == require_org()
    )
    if curriculum_id is not None:
        ccr_stmt = ccr_stmt.where(ChangeRequest.curriculum_id == curriculum_id)
    ccr_rows = (await session.execute(ccr_stmt)).scalars().all()

    # Versions released — join the version_active event to its version so we can
    # honor the optional curriculum filter. Only versions still visible (RLS /
    # auto-filter scoped) contribute.
    version_ids_in_scope = await _version_ids_in_scope(session, curriculum_id)
    active_events = await _events_by_type(session, _VERSION_ACTIVE_EVENT)

    opened: dict[datetime, int] = defaultdict(int)
    released: dict[datetime, int] = defaultdict(int)

    for created_at in ccr_rows:
        opened[_bucket_start(created_at, bucket)] += 1

    for ev in active_events:
        vid = _version_id_from_target(ev.target)
        if vid is None or vid not in version_ids_in_scope:
            continue
        released[_bucket_start(ev.created_at, bucket)] += 1

    all_starts = sorted(set(opened) | set(released))
    return [
        VelocityBucket(
            bucket_start=start,
            ccrs_opened=opened.get(start, 0),
            versions_released=released.get(start, 0),
        )
        for start in all_starts
    ]


# ---------------------------------------------------------------------------
# time_in_state
# ---------------------------------------------------------------------------


async def time_in_state(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID | None = None,
) -> list[StateDuration]:
    """Per-LifecycleStatus mean & median dwell time, from version transitions.

    For each version, the ordered ``version_<status>`` events are walked. The
    dwell time in a state is the gap between entering it and leaving it:
      - entry into ``draft`` is anchored at the version's ``created_at``;
      - every subsequent entry is anchored at the prior transition's timestamp;
      - the exit is the next transition's timestamp.
    The state a version currently sits in (no following transition) is OPEN and
    contributes no completed interval — we never guess its dwell.

    Every LifecycleStatus is returned. States with no completed interval report
    ``n=0`` with ``mean_days=median_days=None`` (honest gap, not a fabrication).
    """
    version_ids_in_scope = await _version_ids_in_scope(session, curriculum_id)

    # Pull all version lifecycle events, ordered, grouped per version.
    stmt = (
        select(HistoryEvent)
        .where(HistoryEvent.event_type.like(f"{_VERSION_EVENT_PREFIX}%"))
        .order_by(HistoryEvent.created_at)
    )
    events = (await session.execute(stmt)).scalars().all()

    # version_id -> ordered list of (entered_status, at_time)
    transitions: dict[uuid.UUID, list[tuple[str, datetime]]] = defaultdict(list)
    for ev in events:
        vid = _version_id_from_target(ev.target)
        if vid is None or vid not in version_ids_in_scope:
            continue
        details = ev.details or {}
        to_status = details.get("to_status")
        if to_status is None:
            # Fall back to parsing the event_type suffix ("version_active").
            to_status = ev.event_type[len(_VERSION_EVENT_PREFIX) :]
        transitions[vid].append((to_status, _as_utc(ev.created_at)))

    # Need each version's created_at to anchor the initial draft interval.
    created_at_by_version = await _version_created_at(session, version_ids_in_scope)

    # state value -> list of completed dwell durations in days
    durations: dict[str, list[float]] = defaultdict(list)

    for vid, seq in transitions.items():
        anchor_status = LifecycleStatus.draft.value
        anchor_time = created_at_by_version.get(vid)
        for entered_status, at_time in seq:
            # The version dwelt in ``anchor_status`` from anchor_time until now.
            if anchor_time is not None:
                delta_days = (at_time - anchor_time).total_seconds() / _SECONDS_PER_DAY
                if delta_days >= 0:
                    durations[anchor_status].append(delta_days)
            anchor_status = entered_status
            anchor_time = at_time
        # The final ``anchor_status`` interval is still open — not counted.

    out: list[StateDuration] = []
    for status in LifecycleStatus:
        samples = durations.get(status.value, [])
        if samples:
            out.append(
                StateDuration(
                    state=status,
                    n=len(samples),
                    mean_days=round(statistics.fmean(samples), 4),
                    median_days=round(statistics.median(samples), 4),
                )
            )
        else:
            out.append(StateDuration(state=status, n=0))
    return out


# ---------------------------------------------------------------------------
# release_cadence
# ---------------------------------------------------------------------------


async def release_cadence(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID | None = None,
) -> CadenceSummary:
    """Count of releases and mean/median days between consecutive releases.

    A "release" is a ``version_active`` event (a version going live), ordered by
    its timestamp. With fewer than two releases there is no gap to measure, so
    the between-days fields are ``None``.
    """
    version_ids_in_scope = await _version_ids_in_scope(session, curriculum_id)
    active_events = await _events_by_type(session, _VERSION_ACTIVE_EVENT)

    release_times = sorted(
        _as_utc(ev.created_at)
        for ev in active_events
        if (vid := _version_id_from_target(ev.target)) is not None
        and vid in version_ids_in_scope
    )

    if len(release_times) < 2:
        return CadenceSummary(releases=len(release_times))

    gaps = [
        (b - a).total_seconds() / _SECONDS_PER_DAY
        for a, b in zip(release_times, release_times[1:])
    ]
    return CadenceSummary(
        releases=len(release_times),
        mean_days_between=round(statistics.fmean(gaps), 4),
        median_days_between=round(statistics.median(gaps), 4),
    )


# ---------------------------------------------------------------------------
# state_distribution
# ---------------------------------------------------------------------------


async def state_distribution(session: AsyncSession) -> list[StateCount]:
    """Current count of CCRs per status and versions per status.

    Returns one row per (entity, status) that has at least one row. Entities are
    ``"ccr"`` and ``"version"``.
    """
    out: list[StateCount] = []

    # Column-only selects escape the entity auto-filter → scope explicitly so the
    # counts never leak another tenant's rows (even under the RLS-bypass superuser).
    ccr_statuses = (
        await session.execute(
            select(ChangeRequest.status).where(
                ChangeRequest.organization_id == require_org()
            )
        )
    ).scalars().all()
    version_statuses = (
        await session.execute(
            select(Version.status).where(Version.organization_id == require_org())
        )
    ).scalars().all()

    for entity, statuses in (("ccr", ccr_statuses), ("version", version_statuses)):
        counts: dict[LifecycleStatus, int] = defaultdict(int)
        for s in statuses:
            counts[s] += 1
        # Stable, status-enum order for deterministic output.
        for status in LifecycleStatus:
            if counts.get(status):
                out.append(StateCount(entity=entity, status=status, count=counts[status]))
    return out


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


async def overview(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID | None = None,
    bucket: str = "week",
) -> AnalyticsOverview:
    """Compose the four aggregates into one dashboard payload."""
    return AnalyticsOverview(
        velocity=await change_velocity(
            session, curriculum_id=curriculum_id, bucket=bucket
        ),
        time_in_state=await time_in_state(session, curriculum_id=curriculum_id),
        cadence=await release_cadence(session, curriculum_id=curriculum_id),
        distribution=await state_distribution(session),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _version_id_from_target(target: str | None) -> uuid.UUID | None:
    """Parse a version UUID from a history event's ``target``.

    lifecycle.transition() sets ``target = str(version.id)`` (raw UUID, no
    prefix). We tolerate a defensive ``version:<uuid>`` form too.
    """
    if not target:
        return None
    raw = target.split(":", 1)[1] if target.startswith("version:") else target
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        return None


async def _events_by_type(session: AsyncSession, event_type: str) -> list[HistoryEvent]:
    stmt = select(HistoryEvent).where(HistoryEvent.event_type == event_type)
    return list((await session.execute(stmt)).scalars().all())


async def _version_ids_in_scope(
    session: AsyncSession, curriculum_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """The set of version ids visible (and optionally curriculum-filtered).

    Going through ``versions`` (rather than trusting event targets) keeps every
    derived metric org-scoped: only versions in the caller's org are counted, so
    another tenant's version_active events can never leak in. Column-only select
    → explicit org guard (the entity auto-filter doesn't apply to it).
    """
    stmt = select(Version.id).where(Version.organization_id == require_org())
    if curriculum_id is not None:
        stmt = stmt.where(Version.curriculum_id == curriculum_id)
    return set((await session.execute(stmt)).scalars().all())


async def _version_created_at(
    session: AsyncSession, version_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    if not version_ids:
        return {}
    # Column-only select → explicit org guard. ``version_ids`` is already
    # org-scoped (from _version_ids_in_scope), but we guard here too so this
    # helper is correct in isolation, independent of DB role.
    stmt = (
        select(Version.id, Version.created_at)
        .where(Version.organization_id == require_org())
        .where(Version.id.in_(version_ids))
    )
    return {
        vid: _as_utc(created_at)
        for vid, created_at in (await session.execute(stmt)).all()
    }
