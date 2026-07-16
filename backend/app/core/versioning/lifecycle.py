"""Lifecycle state machine for Version objects (Task A5).

Operates entirely on in-memory Version instances — no DB session required.
Callers are responsible for persisting the mutated Version and the returned
HistoryEvent.

Transition graph (legal edges only):
    draft  → review
    review → approved
    review → draft          (QA bounce-back)
    approved → active
    active → archived
    archived → sunset

Role gates (who may perform each transition):
    draft → review      : instructor, instructor_lead, architect, program_manager
    review → draft      : qa_lead, architect, program_manager
    review → approved   : qa_lead, architect
    approved → active   : program_manager, architect
    active → archived   : architect, program_manager
    archived → sunset   : architect, program_manager
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent

if TYPE_CHECKING:
    from app.models.version import Version

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalTransition(Exception):
    """Raised when the requested status transition is not a legal edge."""


class PermissionDenied(Exception):
    """Raised when the actor's role is not allowed to perform the transition."""


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------

# fmt: off
TRANSITIONS: dict[LifecycleStatus, set[LifecycleStatus]] = {
    LifecycleStatus.draft:    {LifecycleStatus.review},
    LifecycleStatus.review:   {LifecycleStatus.approved, LifecycleStatus.draft},
    LifecycleStatus.approved: {LifecycleStatus.active},
    LifecycleStatus.active:   {LifecycleStatus.archived},
    LifecycleStatus.archived: {LifecycleStatus.sunset},
    LifecycleStatus.sunset:   set(),  # terminal
}
# fmt: on

# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------

# Keys are (from_status, to_status) tuples; values are the set of roles
# permitted to perform that transition.  Any edge not present defaults to
# deny-all (which effectively means only listed roles can act).
#
# Role strings match the six canonical roles:
#   architect, program_manager, instructor_lead, instructor, qa_lead, devops
# devops: no lifecycle permissions by design (infra role, not curriculum lifecycle)

# fmt: off
ROLE_GATES: dict[tuple[LifecycleStatus, LifecycleStatus], set[str]] = {
    # Writers can submit a draft for review
    (LifecycleStatus.draft,    LifecycleStatus.review):   {"instructor", "instructor_lead", "architect", "program_manager"},
    # QA or architecture can bounce review back to draft
    (LifecycleStatus.review,   LifecycleStatus.draft):    {"qa_lead", "architect", "program_manager"},
    # Only QA/architect may approve; prevents instructors self-approving their work
    (LifecycleStatus.review,   LifecycleStatus.approved): {"qa_lead", "architect"},
    # Programme leadership activates; devops and instructors cannot activate directly
    (LifecycleStatus.approved, LifecycleStatus.active):   {"program_manager", "architect"},
    # Archiving is a lifecycle management action — leadership only
    (LifecycleStatus.active,   LifecycleStatus.archived): {"architect", "program_manager"},
    # Sunsetting is irreversible; restrict to the same leadership group
    (LifecycleStatus.archived, LifecycleStatus.sunset):   {"architect", "program_manager"},
}
# fmt: on


# ---------------------------------------------------------------------------
# Pure graph predicate
# ---------------------------------------------------------------------------


def can_transition(from_status: LifecycleStatus, to_status: LifecycleStatus) -> bool:
    """Return True if `to_status` is a legal successor of `from_status`."""
    return to_status in TRANSITIONS.get(from_status, set())


# ---------------------------------------------------------------------------
# Transition with role check
# ---------------------------------------------------------------------------


def transition(
    version: "Version",
    to_status: LifecycleStatus,
    actor_role: str,
    actor_id: uuid.UUID | None = None,
) -> tuple["Version", HistoryEvent]:
    """Attempt to move *version* to *to_status*.

    Raises:
        IllegalTransition: if the edge (version.status → to_status) is not in TRANSITIONS.
        PermissionDenied: if *actor_role* is not in ROLE_GATES for that edge.

    Returns:
        (version, event) — version.status has been mutated; event is not persisted.
    """
    from_status = version.status

    if not can_transition(from_status, to_status):
        raise IllegalTransition(
            f"Transition {from_status.value!r} → {to_status.value!r} is not allowed."
        )

    allowed_roles = ROLE_GATES.get((from_status, to_status), set())
    if actor_role not in allowed_roles:
        raise PermissionDenied(
            f"Role {actor_role!r} may not perform transition "
            f"{from_status.value!r} → {to_status.value!r}. "
            f"Allowed roles: {sorted(allowed_roles)}"
        )

    version.status = to_status

    event = HistoryEvent(
        actor_id=actor_id,
        event_type=f"version_{to_status.value}",
        target=str(version.id),
        details={
            "from_status": from_status.value,
            "to_status": to_status.value,
            "actor_role": actor_role,
        },
    )

    return version, event


# ---------------------------------------------------------------------------
# Activate helper — supersedes a previously-active version
# ---------------------------------------------------------------------------


def activate(
    new_version: "Version",
    previously_active: "Version | None",
    actor_role: str,
    actor_id: uuid.UUID | None = None,
) -> tuple["Version", "Version | None", list[HistoryEvent]]:
    """Transition *new_version* approved→active and archive *previously_active*.

    Per the spec, activating a new version archives whichever version was
    previously active (if any), ensuring only one active version exists per
    curriculum at a time.

    Raises:
        IllegalTransition: if new_version is not in `approved` status.
        PermissionDenied: if actor_role is not permitted for the approved→active edge.

    Returns:
        (new_version, previously_active_or_None, [events])
        Both version objects have been mutated in place; events are not persisted.
    """
    events: list[HistoryEvent] = []

    # Activate the new version (raises on bad state/role)
    new_version, activate_event = transition(
        new_version, LifecycleStatus.active, actor_role, actor_id=actor_id
    )
    events.append(activate_event)

    # Archive the previous active version (same actor performs both)
    if previously_active is not None:
        previously_active, archive_event = transition(
            previously_active, LifecycleStatus.archived, actor_role, actor_id=actor_id
        )
        events.append(archive_event)

    return new_version, previously_active, events
