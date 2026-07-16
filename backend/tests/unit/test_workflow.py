"""Unit tests for app/core/workflow/rules.py — pure functions, no DB.

Task A7 TDD: write tests first, confirm they fail, then implement.
"""

from __future__ import annotations

import pytest

from app.models.enums import AssetKind
from app.core.versioning.semver import BumpType
from app.core.workflow.rules import (
    QA_DIMENSIONS,
    WorkflowError,
    assert_lo_change_includes_assessment,
    assert_patch_only_mid_cohort,
    assert_qa_complete,
    qa_verdict_valid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_scores() -> dict:
    """Return a dimension_scores dict with all 6 dimensions scored 4."""
    return {dim: 4 for dim in QA_DIMENSIONS}


# ---------------------------------------------------------------------------
# QA_DIMENSIONS constant
# ---------------------------------------------------------------------------

def test_qa_dimensions_has_six_entries():
    assert len(QA_DIMENSIONS) == 6


def test_qa_dimensions_contains_expected_names():
    expected = {
        "content_accuracy",
        "alignment",
        "prerequisites",
        "consistency",
        "instructor_support",
        "student_experience",
    }
    assert set(QA_DIMENSIONS) == expected


# ---------------------------------------------------------------------------
# qa_verdict_valid
# ---------------------------------------------------------------------------

def test_qa_verdict_valid_pass_all_six_dimensions():
    assert qa_verdict_valid(_full_scores(), "pass") is True


def test_qa_verdict_valid_pass_five_dimensions_returns_false():
    scores = _full_scores()
    del scores[QA_DIMENSIONS[0]]
    assert qa_verdict_valid(scores, "pass") is False


def test_qa_verdict_valid_fail_partial_dimensions_ok():
    """A 'fail' verdict does not require all six dimensions."""
    partial = {"content_accuracy": 2}
    assert qa_verdict_valid(partial, "fail") is True


def test_qa_verdict_valid_fail_all_six_also_ok():
    """A 'fail' verdict with all dimensions is also valid."""
    assert qa_verdict_valid(_full_scores(), "fail") is True


def test_qa_verdict_valid_scores_outside_range_invalid():
    """Scores must be 1–5; 0 or 6 make the verdict invalid for 'pass'."""
    bad_scores = _full_scores()
    bad_scores[QA_DIMENSIONS[0]] = 0
    assert qa_verdict_valid(bad_scores, "pass") is False

    bad_scores2 = _full_scores()
    bad_scores2[QA_DIMENSIONS[1]] = 6
    assert qa_verdict_valid(bad_scores2, "pass") is False


# ---------------------------------------------------------------------------
# assert_qa_complete — the enforcing wrapper
# ---------------------------------------------------------------------------

def test_qa_review_six_dimensions_required():
    """
    Spec: verdict=='pass' requires all six dimensions in dimension_scores.
    Partial (5 dims) must raise WorkflowError; all 6 must pass silently.
    """
    # Five dimensions → raises
    five_scores = _full_scores()
    del five_scores[QA_DIMENSIONS[0]]
    with pytest.raises(WorkflowError, match=r"(?i)dimension|missing|pass"):
        assert_qa_complete(five_scores, "pass")

    # All six → no exception
    assert_qa_complete(_full_scores(), "pass")  # should not raise


def test_assert_qa_complete_fail_with_partial_dims_ok():
    """A 'fail' verdict is accepted even with partial dimensions."""
    partial = {"consistency": 1, "alignment": 2}
    assert_qa_complete(partial, "fail")  # should not raise


def test_assert_qa_complete_scores_out_of_range_raises_for_pass():
    """Scores outside 1–5 are rejected for a 'pass' verdict."""
    bad = _full_scores()
    bad[QA_DIMENSIONS[2]] = 6
    with pytest.raises(WorkflowError, match=r"(?i)score|range|1.*5"):
        assert_qa_complete(bad, "pass")


# ---------------------------------------------------------------------------
# assert_patch_only_mid_cohort
# ---------------------------------------------------------------------------

def test_patch_only_mid_cohort():
    """
    Spec 1.1: minor/major bumps while an active cohort exists raise WorkflowError
    unless instructor_override=True. Patch bumps are always allowed.
    """
    # Minor + active cohort → raises
    with pytest.raises(WorkflowError, match=r"(?i)patch|mid.cohort|cohort"):
        assert_patch_only_mid_cohort(BumpType.minor, has_active_cohort=True)

    # Major + active cohort → raises
    with pytest.raises(WorkflowError, match=r"(?i)patch|mid.cohort|cohort"):
        assert_patch_only_mid_cohort(BumpType.major, has_active_cohort=True)

    # Minor + active cohort + instructor_override → passes
    assert_patch_only_mid_cohort(
        BumpType.minor, has_active_cohort=True, instructor_override=True
    )  # should not raise

    # Patch + active cohort → passes
    assert_patch_only_mid_cohort(BumpType.patch, has_active_cohort=True)

    # Minor + NO active cohort → passes
    assert_patch_only_mid_cohort(BumpType.minor, has_active_cohort=False)

    # Major + NO active cohort → passes
    assert_patch_only_mid_cohort(BumpType.major, has_active_cohort=False)


# ---------------------------------------------------------------------------
# assert_lo_change_includes_assessment
# ---------------------------------------------------------------------------

def test_lo_change_forces_assessment_bump():
    """
    Spec 2.2: if learning_objectives is in affected_kinds,
    assessment must also be present.
    """
    # Only LO → raises
    with pytest.raises(WorkflowError, match=r"(?i)assessment|learning.objective"):
        assert_lo_change_includes_assessment({AssetKind.learning_objectives})

    # LO + assessment → passes
    assert_lo_change_includes_assessment(
        {AssetKind.learning_objectives, AssetKind.assessment}
    )  # should not raise


def test_lo_change_no_lo_in_affected_passes():
    """No LO in affected_kinds means no assessment requirement."""
    assert_lo_change_includes_assessment({AssetKind.slides, AssetKind.rubric})


def test_lo_change_empty_set_passes():
    """Empty set has no LO → no requirement triggered."""
    assert_lo_change_includes_assessment(set())
