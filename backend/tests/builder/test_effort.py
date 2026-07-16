"""Unit tests for app.builder.effort — pure, no DB required (Task 5).

Uses simple ``types.SimpleNamespace`` objects as stand-ins for ``DraftItem``
where the full ORM model is not needed.  All arithmetic is checked against the
``DEFAULT_RATES`` spec values:

    review_min_per_slide = 1.0
    min_per_100_loc      = 45.0
    min_per_problem      = 8.0
    study_words_per_minute = 150.0
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.builder.effort import DEFAULT_RATES, course_effort, student_minutes
from app.models.enums import AssetKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(kind, metrics, week_index=None):
    """Build a minimal DraftItem-like namespace."""
    return SimpleNamespace(kind=kind, metrics=metrics, week_index=week_index)


# ---------------------------------------------------------------------------
# student_minutes — one kind per branch
# ---------------------------------------------------------------------------

def test_slides_10_slides():
    """10 slides × 1.0 review_min_per_slide = 10 min."""
    mins = student_minutes(
        AssetKind.slides, {"slide_count": 10}, rates={}
    )
    assert mins == 10


def test_slides_string_kind():
    """Kind may be the raw string value."""
    mins = student_minutes("slides", {"slide_count": 10}, rates={})
    assert mins == 10


def test_lab_300_loc():
    """300 LOC ÷ 100 × 45 = 135 min."""
    mins = student_minutes(AssetKind.lab, {"lines_of_code": 300}, rates={})
    assert mins == 135


def test_project_300_loc():
    """project is in _LOC_KINDS — same formula as lab."""
    mins = student_minutes("project", {"lines_of_code": 300}, rates={})
    assert mins == 135


def test_assessment_5_problems():
    """5 problems × 8 min_per_problem = 40 min."""
    mins = student_minutes(AssetKind.assessment, {"problem_count": 5}, rates={})
    assert mins == 40


def test_lesson_plan_300_words():
    """300 words ÷ 150 wpm = 2.0 → int 2 min."""
    mins = student_minutes(
        AssetKind.lesson_plan, {"word_count": 300}, rates={}
    )
    assert mins == 2


def test_spec_word_count():
    """spec falls through to word-count branch."""
    mins = student_minutes("spec", {"word_count": 450}, rates={})
    assert mins == 3


def test_starter_word_count():
    mins = student_minutes("starter", {"word_count": 150}, rates={})
    assert mins == 1


def test_references_word_count():
    mins = student_minutes("references", {"word_count": 300}, rates={})
    assert mins == 2


def test_learning_objectives_word_count():
    mins = student_minutes("learning_objectives", {"word_count": 150}, rates={})
    assert mins == 1


def test_video_minutes_used_directly():
    """A lesson_plan with video_minutes uses video duration, not word_count."""
    mins = student_minutes(
        AssetKind.lesson_plan, {"video_minutes": 45, "word_count": 9000}, rates={}
    )
    assert mins == 45


def test_video_minutes_on_arbitrary_kind():
    """Any kind with video_minutes uses the video branch."""
    mins = student_minutes("references", {"video_minutes": 20}, rates={})
    assert mins == 20


# ---------------------------------------------------------------------------
# complexity multiplier
# ---------------------------------------------------------------------------

def test_complexity_scales_result():
    """complexity=1.5 multiplies the base estimate."""
    base = student_minutes(AssetKind.slides, {"slide_count": 10}, rates={})
    scaled = student_minutes(
        AssetKind.slides, {"slide_count": 10}, rates={}, complexity=1.5
    )
    assert base == 10
    assert scaled == 15  # 10 × 1.5 = 15


def test_complexity_rounds_correctly():
    """Rounding happens after the multiply (not before)."""
    # 7 slides × 1.0 = 7, × 1.4 = 9.8 → rounds to 10
    mins = student_minutes(
        AssetKind.slides, {"slide_count": 7}, rates={}, complexity=1.4
    )
    assert mins == 10


# ---------------------------------------------------------------------------
# missing / None metrics → 0 contribution, no crash
# ---------------------------------------------------------------------------

def test_missing_slide_count_is_zero():
    assert student_minutes(AssetKind.slides, {}, rates={}) == 0


def test_none_metrics_no_crash():
    assert student_minutes(AssetKind.assessment, None, rates={}) == 0


def test_missing_loc_is_zero():
    assert student_minutes(AssetKind.lab, {}, rates={}) == 0


def test_missing_problem_count_is_zero():
    assert student_minutes(AssetKind.assessment, {}, rates={}) == 0


def test_missing_word_count_is_zero():
    assert student_minutes(AssetKind.lesson_plan, {}, rates={}) == 0


# ---------------------------------------------------------------------------
# custom rate overrides
# ---------------------------------------------------------------------------

def test_custom_review_rate():
    """Caller-supplied rate overrides DEFAULT_RATES per-key."""
    mins = student_minutes(
        AssetKind.slides,
        {"slide_count": 10},
        rates={"review_min_per_slide": 2.0},  # override: 2 × 10 = 20
    )
    assert mins == 20


def test_partial_rate_override_falls_back():
    """Only the overridden key changes; other keys still use DEFAULT_RATES."""
    # Override study_words_per_minute only; problem rate stays at 8.
    mins_problem = student_minutes(
        AssetKind.assessment,
        {"problem_count": 5},
        rates={"study_words_per_minute": 300.0},  # irrelevant here
    )
    assert mins_problem == 40  # still 5 × 8 = 40


# ---------------------------------------------------------------------------
# course_effort — aggregation + bucketing
# ---------------------------------------------------------------------------

def test_course_effort_two_items_same_week():
    """Two items in week 1 are summed; total equals the week sum."""
    items = [
        _item(AssetKind.slides, {"slide_count": 10}, week_index=1),  # 10 min
        _item(AssetKind.assessment, {"problem_count": 5}, week_index=1),  # 40 min
    ]
    result = course_effort(items, objectives=[], rates={})
    assert result["by_week"][1]["student_minutes"] == 50
    assert result["by_week"][1]["item_count"] == 2
    assert result["total_student_minutes"] == 50


def test_course_effort_items_in_different_weeks():
    """Items in different weeks are bucketed separately."""
    items = [
        _item(AssetKind.slides, {"slide_count": 10}, week_index=1),   # 10
        _item(AssetKind.lab, {"lines_of_code": 300}, week_index=2),   # 135
    ]
    result = course_effort(items, objectives=[], rates={})
    assert result["by_week"][1]["student_minutes"] == 10
    assert result["by_week"][2]["student_minutes"] == 135
    assert result["total_student_minutes"] == 145


def test_course_effort_null_week_goes_to_bucket_zero():
    """week_index=None items are placed under key 0 (unscheduled bucket)."""
    items = [
        _item(AssetKind.lesson_plan, {"word_count": 300}, week_index=None),  # 2 min
    ]
    result = course_effort(items, objectives=[], rates={})
    assert 0 in result["by_week"]
    assert result["by_week"][0]["student_minutes"] == 2


def test_course_effort_total_includes_all_weeks():
    """total_student_minutes sums across all week buckets including 0."""
    items = [
        _item(AssetKind.slides, {"slide_count": 10}, week_index=1),   # 10
        _item(AssetKind.lesson_plan, {"word_count": 300}, week_index=None),  # 2
    ]
    result = course_effort(items, objectives=[], rates={})
    assert result["total_student_minutes"] == 12


def test_course_effort_empty_inputs():
    """Empty item list returns empty by_week and zero total."""
    result = course_effort([], objectives=[], rates={})
    assert result["by_week"] == {}
    assert result["total_student_minutes"] == 0
