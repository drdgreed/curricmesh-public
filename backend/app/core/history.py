"""Audit history helpers — Task A6.

Provides:
  - EventType: canonical audit-vocabulary constants.
  - record(): construct + persist a HistoryEvent in one call.
  - persist_event(): bridge for already-built HistoryEvents (e.g. from lifecycle.transition()).

Design note on event-type vocabulary
-------------------------------------
lifecycle.transition() emits fine-grained "version_<status>" events (e.g.
"version_review", "version_approved", "version_active", "version_archived",
"version_sunset").  EventType below exposes the same strings as named constants
so call-sites can import from one place rather than constructing raw strings.

Higher-level business events (ccr_created, asset_updated, etc.) are emitted by
service-layer code in A7/A8; lifecycle.py itself stays a pure state machine.

Session contract
-----------------
Both helpers call ``await session.flush()`` so that server-generated defaults
(id via uuid4, created_at via server_default=func.now()) are populated before
returning.  They do NOT commit — the caller controls the transaction boundary.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.history import HistoryEvent


# ---------------------------------------------------------------------------
# Event-type vocabulary
# ---------------------------------------------------------------------------


class EventType(enum.StrEnum):
    """Canonical audit-event vocabulary for HistoryEvent.event_type.

    Because this is a StrEnum, every member IS its string value — no
    `.value` access needed and equality comparisons against plain strings
    work transparently (e.g. ``row.event_type == EventType.version_active``).

    ## Version lifecycle transitions
    For any version lifecycle transition, the canonical audit record is the
    fine-grained ``version_<status>`` event emitted by
    ``lifecycle.transition()``.  The A7 service layer MUST persist that
    event via ``persist_event()`` and MUST NOT also emit a duplicate alias
    (e.g. ``version_activated``) for the same action.  One transition →
    one audit row.

    Fine-grained events (emitted by ``lifecycle.transition()``):
        version_review     — draft → review  (submitted for QA)
        version_draft      — review → draft  (QA bounce-back)
        version_approved   — review → approved
        version_active     — approved → active
        version_archived   — active → archived
        version_sunset     — archived → sunset

    ## Domain alias events
    The alias members below are for distinct domain actions that have NO
    corresponding lifecycle transition.  They are NOT synonyms for
    lifecycle events; using them alongside a lifecycle event for the same
    action would produce duplicate audit rows.

        ccr_created        — a Change Control Request was created
        asset_updated      — an Asset or AssetVersion was modified
        owner_updated      — curriculum ownership changed
        cohort_updated     — cohort membership or schedule changed

    ## Legacy aliases (kept for backward compat — do NOT use in new code)
        version_submitted  — superseded by version_review
        qa_approved        — superseded by version_approved
        version_activated  — superseded by version_active
    """

    # --- lifecycle state-machine events (match lifecycle.transition() output) ---
    version_review = "version_review"
    version_draft = "version_draft"
    version_approved = "version_approved"
    version_active = "version_active"
    version_archived = "version_archived"
    version_sunset = "version_sunset"

    # --- distinct domain-action events (no lifecycle counterpart) ------------
    ccr_created = "ccr_created"
    asset_updated = "asset_updated"
    owner_updated = "owner_updated"
    cohort_updated = "cohort_updated"

    # --- first-class domain events (not lifecycle transitions) ---------------
    qa_passed = "qa_passed"

    # Executable release (Phase C) — a fork() produced + activated a new
    # immutable CurriculumVersion. Target is "curriculum:<uuid>".
    curriculum_released = "curriculum_released"

    # --- legacy aliases (backward compat only — prefer fine-grained above) ---
    version_submitted = "version_submitted"
    qa_approved = "qa_approved"
    version_activated = "version_activated"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def record(
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    event_type: str | EventType,
    target: str,
    details: dict[str, Any] | None = None,
) -> HistoryEvent:
    """Construct, persist, and return a HistoryEvent.

    Args:
        session:    Active AsyncSession — caller owns the transaction.
        actor_id:   UUID of the acting user, or None for system events.
        event_type: One of the EventType constants (or any arbitrary string).
        target:     Free-form reference to the affected entity, e.g. "version:<uuid>".
        details:    Arbitrary JSONB payload; defaults to {}.

    Returns:
        The newly persisted HistoryEvent with id and created_at populated.
    """
    event = HistoryEvent(
        actor_id=actor_id,
        event_type=event_type,
        target=target,
        details=details if details is not None else {},
    )
    session.add(event)
    await session.flush()
    return event


async def persist_event(
    session: AsyncSession,
    event: HistoryEvent,
) -> HistoryEvent:
    """Persist an already-constructed HistoryEvent (e.g. from lifecycle.transition()).

    This is the bridge between the pure state machine (which builds HistoryEvents
    but never touches the DB) and the persistence layer.

    Args:
        session: Active AsyncSession — caller owns the transaction.
        event:   A HistoryEvent instance not yet added to any session.

    Returns:
        The same HistoryEvent with id and created_at populated.
    """
    session.add(event)
    await session.flush()
    return event
