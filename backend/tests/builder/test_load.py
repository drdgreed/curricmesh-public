"""Unit tests for app.builder.load — pure, no DB required (Task 6).

Exercises ``week_flags`` for:
  * overload True / False relative to the weekly_hours_target
  * density_warn True / False relative to density_threshold
  * weeks sorted ascending
  * both int-count and list inputs for objectives_by_week / items_by_week
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.builder.load import HARD_KINDS, week_flags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _effort(student_minutes: int) -> dict:
    """Minimal effort_by_week entry."""
    return {"student_minutes": student_minutes, "item_count": 1}


def _item(kind: str):
    return SimpleNamespace(kind=kind)


# ---------------------------------------------------------------------------
# Overload flag
# ---------------------------------------------------------------------------

def test_overload_true_600_min_target_8h():
    """600 min = 10 h > 8 h target → overload True."""
    flags = week_flags(
        effort_by_week={1: _effort(600)},
        objectives_by_week={},
        items_by_week={},
        weekly_hours_target=8.0,
    )
    assert len(flags) == 1
    row = flags[0]
    assert row["week"] == 1
    assert row["student_hours"] == round(600 / 60, 2)
    assert row["overload"] is True


def test_overload_false_light_week():
    """120 min = 2 h < 8 h target → overload False."""
    flags = week_flags(
        effort_by_week={1: _effort(120)},
        objectives_by_week={},
        items_by_week={},
        weekly_hours_target=8.0,
    )
    assert flags[0]["overload"] is False


def test_overload_exactly_at_target_is_not_overload():
    """Exactly at target (not strictly greater) → overload False."""
    flags = week_flags(
        effort_by_week={1: _effort(480)},   # exactly 8 h
        objectives_by_week={},
        items_by_week={},
        weekly_hours_target=8.0,
    )
    assert flags[0]["overload"] is False


# ---------------------------------------------------------------------------
# Density flag — objective count
# ---------------------------------------------------------------------------

def test_density_warn_true_5_objectives():
    """5 objectives > threshold 4 → density_warn True."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 5},
        items_by_week={},
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["density_warn"] is True
    assert flags[0]["new_concepts"] == 5


def test_density_warn_false_3_objectives():
    """3 objectives ≤ threshold 4 → density_warn False."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 3},
        items_by_week={},
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["density_warn"] is False
    assert flags[0]["new_concepts"] == 3


def test_density_warn_exactly_at_threshold_is_not_warn():
    """Exactly at threshold (not strictly greater) → density_warn False."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 4},
        items_by_week={},
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["density_warn"] is False


# ---------------------------------------------------------------------------
# Density flag — hard items contribution
# ---------------------------------------------------------------------------

def test_hard_items_added_to_new_concepts():
    """2 objectives + 1 lab = 3 new_concepts → no warn at threshold 4."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 2},
        items_by_week={1: [_item("lab"), _item("slides")]},  # only lab counts
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["new_concepts"] == 3
    assert flags[0]["density_warn"] is False


def test_hard_items_trigger_density_warn():
    """3 objectives + 2 hard items = 5 > 4 → density_warn True."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 3},
        items_by_week={1: [_item("lab"), _item("assessment"), _item("slides")]},
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["new_concepts"] == 5
    assert flags[0]["density_warn"] is True


def test_items_by_week_as_int_count():
    """When items_by_week value is an int, treat it as the hard-item count."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 2},
        items_by_week={1: 3},   # pre-counted hard items
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["new_concepts"] == 5
    assert flags[0]["density_warn"] is True


def test_objectives_by_week_as_list():
    """objectives_by_week may supply a list; its length is the count."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: ["obj-a", "obj-b", "obj-c", "obj-d", "obj-e"]},
        items_by_week={},
        weekly_hours_target=10.0,
        density_threshold=4,
    )
    assert flags[0]["new_concepts"] == 5
    assert flags[0]["density_warn"] is True


# ---------------------------------------------------------------------------
# Hard-kind membership
# ---------------------------------------------------------------------------

def test_hard_kinds_set():
    """Verify HARD_KINDS contains exactly the three expected values."""
    assert HARD_KINDS == {"lab", "project", "assessment"}


def test_project_is_hard_kind():
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 0},
        items_by_week={1: [_item("project")]},
        weekly_hours_target=10.0,
        density_threshold=0,
    )
    assert flags[0]["new_concepts"] == 1


# ---------------------------------------------------------------------------
# Weeks sorted + multi-week
# ---------------------------------------------------------------------------

def test_weeks_sorted_ascending():
    """Output rows are sorted by week ascending regardless of input ordering."""
    flags = week_flags(
        effort_by_week={3: _effort(60), 1: _effort(60), 2: _effort(60)},
        objectives_by_week={},
        items_by_week={},
        weekly_hours_target=10.0,
    )
    assert [r["week"] for r in flags] == [1, 2, 3]


def test_weeks_union_of_all_inputs():
    """Weeks that appear in any input are all included."""
    flags = week_flags(
        effort_by_week={1: _effort(60)},
        objectives_by_week={2: 2},
        items_by_week={3: 1},
        weekly_hours_target=10.0,
    )
    assert [r["week"] for r in flags] == [1, 2, 3]


def test_missing_week_in_effort_has_zero_hours():
    """A week in objectives_by_week but not effort_by_week has 0 student_hours."""
    flags = week_flags(
        effort_by_week={},
        objectives_by_week={1: 3},
        items_by_week={},
        weekly_hours_target=10.0,
    )
    assert flags[0]["student_hours"] == 0.0
    assert flags[0]["overload"] is False


# ---------------------------------------------------------------------------
# Empty inputs
# ---------------------------------------------------------------------------

def test_empty_inputs_returns_empty_list():
    result = week_flags(
        effort_by_week={},
        objectives_by_week={},
        items_by_week={},
        weekly_hours_target=8.0,
    )
    assert result == []
