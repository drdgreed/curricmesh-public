# backend/tests/unit/test_lifecycle.py
"""Unit tests for the lifecycle state machine (Task A5).

All tests operate on in-memory Version instances — no DB session needed.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import LifecycleStatus
from app.models.history import HistoryEvent
from app.models.version import Version
from app.core.versioning.lifecycle import (
    TRANSITIONS,
    ROLE_GATES,
    IllegalTransition,
    PermissionDenied,
    can_transition,
    transition,
    activate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_version(status: LifecycleStatus) -> object:
    """Construct a minimal in-memory Version-like object without a DB session."""
    # We need to avoid SQLAlchemy's instrumentation requiring a session, so we
    # build a plain object that satisfies the fields lifecycle.py touches:
    # .id, .status
    class _FakeVersion:
        def __init__(self, s: LifecycleStatus) -> None:
            self.id = uuid.uuid4()
            self.status = s

    return _FakeVersion(status)


# ---------------------------------------------------------------------------
# Real-model smoke test
# ---------------------------------------------------------------------------

def test_transition_works_on_real_version_model():
    """transition() works on a real Version instance (no DB session required)."""
    v = Version(
        curriculum_id=uuid.uuid4(),
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    v.id = uuid.uuid4()

    result_v, event = transition(v, LifecycleStatus.review, "instructor")

    assert result_v.status == LifecycleStatus.review
    assert isinstance(event, HistoryEvent)


# ---------------------------------------------------------------------------
# can_transition — pure graph check
# ---------------------------------------------------------------------------

def test_can_transition_draft_to_review():
    assert can_transition(LifecycleStatus.draft, LifecycleStatus.review) is True


def test_can_transition_draft_to_active_is_false():
    assert can_transition(LifecycleStatus.draft, LifecycleStatus.active) is False


def test_can_transition_review_to_draft_bounce_back():
    """QA can bounce review back to draft."""
    assert can_transition(LifecycleStatus.review, LifecycleStatus.draft) is True


def test_can_transition_review_to_approved():
    assert can_transition(LifecycleStatus.review, LifecycleStatus.approved) is True


def test_can_transition_approved_to_active():
    assert can_transition(LifecycleStatus.approved, LifecycleStatus.active) is True


def test_can_transition_active_to_archived():
    assert can_transition(LifecycleStatus.active, LifecycleStatus.archived) is True


def test_can_transition_archived_to_sunset():
    assert can_transition(LifecycleStatus.archived, LifecycleStatus.sunset) is True


def test_can_transition_no_skip_draft_to_approved():
    assert can_transition(LifecycleStatus.draft, LifecycleStatus.approved) is False


def test_can_transition_no_backward_active_to_review():
    assert can_transition(LifecycleStatus.active, LifecycleStatus.review) is False


# ---------------------------------------------------------------------------
# transition — happy-path and error cases
# ---------------------------------------------------------------------------

def test_legal_transition_draft_to_review():
    """transition(draft→review, instructor) succeeds; returns (version, event)."""
    v = make_version(LifecycleStatus.draft)
    actor = uuid.uuid4()

    result_v, event = transition(v, LifecycleStatus.review, "instructor", actor_id=actor)

    assert result_v.status == LifecycleStatus.review
    assert event is not None
    assert event.actor_id == actor
    assert event.event_type == "version_review"
    assert event.target == str(v.id)


def test_illegal_transition_draft_to_active_raises():
    """Skipping states raises IllegalTransition regardless of role."""
    v = make_version(LifecycleStatus.draft)
    with pytest.raises(IllegalTransition, match=r"(?i)draft.*active|illegal"):
        transition(v, LifecycleStatus.active, "architect")


def test_illegal_transition_active_to_draft_raises():
    """No backward transitions (except review→draft) raise IllegalTransition."""
    v = make_version(LifecycleStatus.active)
    with pytest.raises(IllegalTransition):
        transition(v, LifecycleStatus.draft, "architect")


def test_review_to_approved_requires_qa_lead_or_architect():
    """review→approved requires qa_lead or architect; instructor is denied."""
    v_qa = make_version(LifecycleStatus.review)
    result_v, event = transition(v_qa, LifecycleStatus.approved, "qa_lead")
    assert result_v.status == LifecycleStatus.approved

    v_arch = make_version(LifecycleStatus.review)
    result_v2, _ = transition(v_arch, LifecycleStatus.approved, "architect")
    assert result_v2.status == LifecycleStatus.approved

    v_inst = make_version(LifecycleStatus.review)
    with pytest.raises(PermissionDenied, match=r"(?i)instructor|not allowed|permission"):
        transition(v_inst, LifecycleStatus.approved, "instructor")


def test_review_to_draft_bounce_back_allowed_for_qa_lead():
    """QA bounce-back (review→draft) is allowed for qa_lead."""
    v = make_version(LifecycleStatus.review)
    result_v, event = transition(v, LifecycleStatus.draft, "qa_lead")
    assert result_v.status == LifecycleStatus.draft
    assert event.event_type == "version_draft"


def test_approved_to_active_requires_program_manager_or_architect():
    """approved→active requires program_manager or architect; qa_lead is denied."""
    v_pm = make_version(LifecycleStatus.approved)
    result_v, _ = transition(v_pm, LifecycleStatus.active, "program_manager")
    assert result_v.status == LifecycleStatus.active

    v_qa = make_version(LifecycleStatus.approved)
    with pytest.raises(PermissionDenied):
        transition(v_qa, LifecycleStatus.active, "qa_lead")


def test_history_event_details_contains_transition_info():
    """HistoryEvent.details should include from/to status info."""
    v = make_version(LifecycleStatus.draft)
    _, event = transition(v, LifecycleStatus.review, "instructor_lead")

    assert event.details is not None
    assert event.details.get("from_status") == "draft"
    assert event.details.get("to_status") == "review"


# ---------------------------------------------------------------------------
# activate — supersede + archive previous
# ---------------------------------------------------------------------------

def test_active_supersede_archives_previous():
    """activate(new=approved, prev=active) => new=active, prev=archived, 2 events."""
    new_v = make_version(LifecycleStatus.approved)
    prev_v = make_version(LifecycleStatus.active)

    new_out, prev_out, events = activate(new_v, prev_v, "program_manager")

    assert new_out.status == LifecycleStatus.active
    assert prev_out.status == LifecycleStatus.archived
    assert len(events) == 2


def test_activate_no_previous_returns_none_prev():
    """activate with no prior active version leaves prev as None."""
    new_v = make_version(LifecycleStatus.approved)

    new_out, prev_out, events = activate(new_v, None, "architect")

    assert new_out.status == LifecycleStatus.active
    assert prev_out is None
    assert len(events) == 1


def test_activate_wrong_starting_status_raises():
    """activate from a non-approved version raises IllegalTransition."""
    new_v = make_version(LifecycleStatus.draft)
    with pytest.raises(IllegalTransition):
        activate(new_v, None, "architect")


def test_activate_wrong_role_raises():
    """activate by an unauthorized role raises PermissionDenied."""
    new_v = make_version(LifecycleStatus.approved)
    with pytest.raises(PermissionDenied):
        activate(new_v, None, "instructor")
