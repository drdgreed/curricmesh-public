"""Deterministic effort estimator for the Course Builder (Task 5).

Computes student *out-of-class* minutes for each ``DraftItem`` based on its
``kind`` and ``metrics`` JSONB.  All maths are pure / side-effect-free — no DB,
no network, no state.  The ``course_effort`` aggregator groups those per-item
estimates by ``week_index``.

Null-week bucketing choice
--------------------------
Items whose ``week_index`` is ``None`` are placed under the integer key ``0``
(zero).  This makes the ``by_week`` dict always have homogeneous ``int`` keys,
which simplifies downstream JSON serialisation and ``week_flags`` iteration.
Week 0 is otherwise unused (real weeks start at 1) so it is an unambiguous
"unscheduled" bucket.  Callers that care about scheduled vs unscheduled can
check ``by_week.get(0)``.

Kind → rate mapping
-------------------
``slides``                → ``review_min_per_slide * slide_count``
``lab`` / ``project``     → ``(lines_of_code / 100) * min_per_100_loc``
``assessment``            → ``problem_count * min_per_problem``
kinds with video          → ``video_minutes``  (``lesson_plan`` can embed video)
everything else           → ``word_count / study_words_per_minute``

The "video" check looks for a non-zero ``video_minutes`` metric *before*
falling through to the word-count branch, so a ``lesson_plan`` that has
``video_minutes`` set will use the video rule.

``AssetKind`` enum values or their raw ``.value`` strings are both accepted —
``getattr(kind, "value", kind)`` normalises both to a plain string.
"""

from __future__ import annotations

from typing import Any

DEFAULT_RATES: dict[str, float] = {
    "present_min_per_slide": 2.0,
    "review_min_per_slide": 1.0,
    "study_words_per_minute": 150.0,
    "min_per_100_loc": 45.0,
    "min_per_problem": 8.0,
}

# Kinds for which we use the code-complexity (LOC) rule.
_LOC_KINDS = {"lab", "project"}


def _rate(key: str, rates: dict[str, float]) -> float:
    """Return the caller-supplied rate for *key*, falling back to DEFAULT_RATES."""
    return rates.get(key, DEFAULT_RATES[key])


def student_minutes(
    kind: Any,
    metrics: dict[str, Any],
    rates: dict[str, float],
    complexity: float = 1.0,
) -> int:
    """Out-of-class student minutes for one item.

    Parameters
    ----------
    kind:
        ``AssetKind`` enum member **or** its ``.value`` string (e.g. ``"slides"``).
    metrics:
        The item's ``metrics`` JSONB dict.  Missing keys are treated as 0 — the
        function never raises on absent metrics.
    rates:
        Caller-supplied rate overrides.  Keys absent here fall back to
        ``DEFAULT_RATES`` on a per-key basis.
    complexity:
        AI complexity multiplier (default 1.0).  The final result is multiplied
        by this value before rounding.

    Returns
    -------
    int
        Minutes rounded to the nearest whole number.
    """
    kind_str: str = getattr(kind, "value", kind)
    m = metrics or {}

    if kind_str == "slides":
        raw = _rate("review_min_per_slide", rates) * float(m.get("slide_count") or 0)

    elif kind_str in _LOC_KINDS:
        loc = float(m.get("lines_of_code") or 0)
        raw = (loc / 100.0) * _rate("min_per_100_loc", rates)

    elif kind_str == "assessment":
        raw = float(m.get("problem_count") or 0) * _rate("min_per_problem", rates)

    else:
        # Video check: any kind that carries video_minutes uses duration directly.
        video = float(m.get("video_minutes") or 0)
        if video > 0:
            raw = video
        else:
            # Text-based: lesson_plan, spec, starter, references,
            # learning_objectives, rubric, and any future kinds.
            wpm = _rate("study_words_per_minute", rates)
            words = float(m.get("word_count") or 0)
            raw = words / wpm if wpm > 0 else 0.0

    return int(round(raw * complexity))


def course_effort(
    items: Any,
    objectives: Any,
    rates: dict[str, float],
) -> dict[str, Any]:
    """Aggregate per-item effort into per-week and course totals.

    Parameters
    ----------
    items:
        Iterable of ``DraftItem``-like objects with ``.kind``, ``.metrics``
        (dict or ``None``), and ``.week_index`` (int or ``None``).
    objectives:
        Iterable of ``DraftObjective``-like objects.  Currently unused by the
        effort calc (included for future in-class/objective-level tracking and
        to match the public API the router expects).
    rates:
        Rate overrides; per-key fallback to ``DEFAULT_RATES``.

    Returns
    -------
    dict with shape::

        {
            "by_week": {
                1: {"student_minutes": 135, "item_count": 2},
                0: {"student_minutes": 10, "item_count": 1},  # unscheduled
            },
            "total_student_minutes": 145,
        }

    Null ``week_index`` items are bucketed under key ``0`` (see module docstring).
    """
    by_week: dict[int, dict[str, int]] = {}

    for item in items:
        week: int = item.week_index if item.week_index is not None else 0
        mins = student_minutes(
            kind=item.kind,
            metrics=item.metrics or {},
            rates=rates,
        )
        if week not in by_week:
            by_week[week] = {"student_minutes": 0, "item_count": 0}
        by_week[week]["student_minutes"] += mins
        by_week[week]["item_count"] += 1

    total = sum(w["student_minutes"] for w in by_week.values())
    return {"by_week": by_week, "total_student_minutes": total}
