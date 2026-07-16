"""Cognitive-load / overload detector for the Course Builder (Task 6).

``week_flags`` is a pure, deterministic function: it reads the aggregated effort
output from ``course_effort`` and two week→count (or week→list) mappings, then
emits one flag row per week covering:

* **overload** — student out-of-class hours exceed the author's weekly target.
* **density_warn** — the number of *new concepts* introduced that week exceeds a
  CLT working-memory threshold.

New-concept count
-----------------
``new_concepts = (# objectives in that week) + (# items whose kind is in
HARD_KINDS in that week)``.  ``HARD_KINDS = {"lab", "project", "assessment"}``
— these are the item types that introduce significant novel cognitive load rather
than reinforcing existing concepts.

Input shapes for objectives_by_week / items_by_week
----------------------------------------------------
Both mappings accept **either** a ``week → int`` (count) **or** a
``week → list`` (any list, its length is used).  This lets callers pass raw
counts or the actual item/objective lists interchangeably.

Week key coverage
-----------------
All weeks that appear in *any* of the three inputs are included in the output.
Missing weeks in a given input are treated as zero for that dimension.

The output list is sorted ascending by week number.
"""

from __future__ import annotations

from typing import Any

HARD_KINDS: set[str] = {"lab", "project", "assessment"}


def _count(value: Any) -> int:
    """Return an integer count from either an int/None or a list."""
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    return int(value)


def _kind_str(kind: Any) -> str:
    """Normalise AssetKind enum or string to a plain string."""
    return getattr(kind, "value", kind)


def week_flags(
    effort_by_week: dict[int, dict[str, Any]],
    objectives_by_week: dict[int, Any],
    items_by_week: dict[int, Any],
    weekly_hours_target: float,
    density_threshold: int = 4,
) -> list[dict[str, Any]]:
    """Compute overload and concept-density flags for each week.

    Parameters
    ----------
    effort_by_week:
        The ``"by_week"`` sub-dict from ``course_effort()``.  Shape::

            {week_int: {"student_minutes": int, "item_count": int}, ...}

    objectives_by_week:
        Mapping of ``week_int → int`` **or** ``week_int → list``.  Counts (or
        list-lengths) of new objectives introduced that week.

    items_by_week:
        Mapping of ``week_int → int`` **or** ``week_int → list[item-like]``.
        When a list is supplied, only items whose ``.kind`` (or ``["kind"]``) is
        in ``HARD_KINDS`` are counted; when an int is supplied it is taken as
        the pre-counted hard-item count for that week.

    weekly_hours_target:
        The learner profile's target (e.g. ``8.0``).  Overload fires when
        ``student_hours > weekly_hours_target``.

    density_threshold:
        Maximum acceptable ``new_concepts`` count before ``density_warn`` is
        set (default 4).  Warn fires when ``new_concepts > density_threshold``.

    Returns
    -------
    list[dict]
        One entry per week, sorted ascending by ``week``.  Each entry::

            {
                "week": int,
                "student_hours": float,   # rounded to 2 dp
                "overload": bool,
                "new_concepts": int,
                "density_warn": bool,
            }
    """
    # Collect the full set of weeks across all three inputs.
    all_weeks: set[int] = (
        set(effort_by_week.keys())
        | set(objectives_by_week.keys())
        | set(items_by_week.keys())
    )

    results: list[dict[str, Any]] = []
    for week in sorted(all_weeks):
        student_minutes = (effort_by_week.get(week) or {}).get("student_minutes", 0)
        student_hours = round(student_minutes / 60.0, 2)
        overload = student_hours > weekly_hours_target

        # Objective count for this week.
        obj_count = _count(objectives_by_week.get(week))

        # Hard-item count for this week.
        items_val = items_by_week.get(week)
        if items_val is None:
            hard_count = 0
        elif isinstance(items_val, list):
            # List of item-like objects or dicts — count HARD_KINDS.
            hard_count = sum(
                1
                for it in items_val
                if _kind_str(
                    it.get("kind") if isinstance(it, dict) else getattr(it, "kind", "")
                )
                in HARD_KINDS
            )
        else:
            # Pre-counted int: treat as the hard-item count directly.
            hard_count = int(items_val)

        new_concepts = obj_count + hard_count
        density_warn = new_concepts > density_threshold

        results.append(
            {
                "week": week,
                "student_hours": student_hours,
                "overload": overload,
                "new_concepts": new_concepts,
                "density_warn": density_warn,
            }
        )

    return results
