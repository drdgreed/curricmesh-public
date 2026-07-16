"""Pydantic v2 schemas for V3-A change-velocity & time-in-state analytics.

Read-only aggregates over existing tables (change_requests, history_events,
versions, approvals). No new persistence — these mirror the engine's return
shapes in app/services/analytics.py.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import LifecycleStatus


class VelocityBucket(BaseModel):
    """CCRs opened and versions released within one time bucket (week/month)."""

    bucket_start: datetime
    ccrs_opened: int
    versions_released: int


class StateDuration(BaseModel):
    """Mean/median dwell time in a lifecycle state.

    ``n`` is the number of completed (entered-and-exited) intervals observed for
    this state. When transition data is too sparse to reconstruct any interval,
    ``n == 0`` and the durations are ``None`` — the gap is surfaced honestly
    rather than fabricated.
    """

    state: LifecycleStatus
    n: int
    mean_days: float | None = None
    median_days: float | None = None


class CadenceSummary(BaseModel):
    """Release cadence: how many releases and the gap between consecutive ones."""

    releases: int
    mean_days_between: float | None = None
    median_days_between: float | None = None


class StateCount(BaseModel):
    """Current count of an entity (``ccr`` or ``version``) in a given status."""

    entity: str
    status: LifecycleStatus
    count: int


class AnalyticsOverview(BaseModel):
    """Composed payload for the Analytics dashboard."""

    velocity: list[VelocityBucket]
    time_in_state: list[StateDuration]
    cadence: CadenceSummary
    distribution: list[StateCount]
