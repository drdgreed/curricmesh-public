"""Pure business-rule guards for the CurricMesh workflow engine — Task A7.

All functions in this module are pure (no DB access, no side effects).
They raise WorkflowError on constraint violations so callers can treat
any workflow precondition failure as the same exception type.

Rule reference:
  1.1  Only patch bumps may ship mid-cohort (instructor_override bypasses).
  2.2  Learning-objectives changes structurally require an assessment change
       (full dependency cascade is Milestone B; this is the guard-only).
  QA   A 'pass' verdict requires all six dimensions scored 1–5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.versioning.semver import BumpType
    from app.models.enums import AssetKind


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class WorkflowError(Exception):
    """Raised when a workflow business-rule constraint is violated."""


# ---------------------------------------------------------------------------
# QA dimensions constant
# ---------------------------------------------------------------------------

QA_DIMENSIONS: tuple[str, ...] = (
    "content_accuracy",
    "alignment",
    "prerequisites",
    "consistency",
    "instructor_support",
    "student_experience",
)


# ---------------------------------------------------------------------------
# QA rules
# ---------------------------------------------------------------------------


def qa_verdict_valid(dimension_scores: dict, verdict: str) -> bool:
    """Return True if the dimension_scores + verdict combination is coherent.

    Rules:
      - verdict == 'pass' requires ALL six QA_DIMENSIONS present, each scored
        as an integer in [1, 5].
      - verdict == 'fail' (or any non-pass verdict) may be partial; only
        dimensions that are present are validated for range.

    Returns False rather than raising; use assert_qa_complete() for the
    enforcing wrapper.
    """
    if verdict == "pass":
        # All six must be present
        for dim in QA_DIMENSIONS:
            if dim not in dimension_scores:
                return False
        # All present scores must be integers in [1, 5]
        for dim in QA_DIMENSIONS:
            score = dimension_scores[dim]
            if not isinstance(score, int) or isinstance(score, bool):
                return False
            if score < 1 or score > 5:
                return False
    else:
        # For non-pass verdicts validate any provided scores for range
        for score in dimension_scores.values():
            if isinstance(score, int) and not isinstance(score, bool):
                if score < 1 or score > 5:
                    return False

    return True


def assert_qa_complete(dimension_scores: dict, verdict: str) -> None:
    """Enforce QA completeness; raise WorkflowError if qa_verdict_valid() is False.

    Delegates to qa_verdict_valid() for the full predicate check, so both
    'pass' (all 6 dims, scores in 1–5) and 'fail' (any provided score must be
    in range) are enforced consistently.

    Args:
        dimension_scores: mapping of dimension name → integer score (1–5).
        verdict:          'pass' or 'fail'.

    Raises:
        WorkflowError: if verdict is 'pass' and any dimension is missing,
                       or if any present score is outside the 1–5 int range.
    """
    if verdict == "pass":
        missing = [d for d in QA_DIMENSIONS if d not in dimension_scores]
        if missing:
            raise WorkflowError(
                f"QA verdict 'pass' requires all six dimensions. "
                f"Missing: {missing}"
            )

    if not qa_verdict_valid(dimension_scores, verdict):
        out_of_range = [
            d for d, v in dimension_scores.items()
            if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= 5)
        ]
        raise WorkflowError(
            f"QA scores must be integers in range 1–5. "
            f"Out-of-range dimensions: {out_of_range}"
        )


# ---------------------------------------------------------------------------
# Mid-cohort bump rule
# ---------------------------------------------------------------------------


def assert_patch_only_mid_cohort(
    proposed_bump: "BumpType",
    has_active_cohort: bool,
    instructor_override: bool = False,
) -> None:
    """Enforce that only patch changes may ship while a cohort is active.

    Spec rule 1.1: if proposed_bump is 'minor' or 'major' AND has_active_cohort
    AND NOT instructor_override → raise WorkflowError.

    Args:
        proposed_bump:      The BumpType (major/minor/patch) being proposed.
        has_active_cohort:  True if any cohort tied to this curriculum is
                            currently active.
        instructor_override: When True, the instructor has explicitly bypassed
                             this guard (requires deliberate UI action).

    Raises:
        WorkflowError: if the constraints above are violated.
    """
    from app.core.versioning.semver import BumpType  # local import — rules.py stays pure

    if (
        proposed_bump in (BumpType.minor, BumpType.major)
        and has_active_cohort
        and not instructor_override
    ):
        raise WorkflowError(
            f"Only patch changes may ship mid-cohort. "
            f"Proposed bump '{proposed_bump.value}' is not allowed while a "
            f"cohort is active. Use instructor_override=True to bypass."
        )


# ---------------------------------------------------------------------------
# LO → assessment structural guard
# ---------------------------------------------------------------------------


def assert_lo_change_includes_assessment(affected_kinds: "set[AssetKind]") -> None:
    """Enforce that learning-objectives changes include an assessment change.

    Spec rule 2.2: if AssetKind.learning_objectives is in affected_kinds then
    AssetKind.assessment must also be present.

    Note: The full dependency-cascade graph is Milestone B. This function is
    the structural guard only — it does not resolve transitive dependencies.

    Args:
        affected_kinds: Set of AssetKind values describing which asset types
                        will be modified by this change request.

    Raises:
        WorkflowError: if learning_objectives is affected but assessment is not.
    """
    from app.models.enums import AssetKind  # local import — keeps rules.py pure

    if AssetKind.learning_objectives in affected_kinds:
        if AssetKind.assessment not in affected_kinds:
            raise WorkflowError(
                "A learning-objectives change requires a corresponding assessment "
                "change. Add AssetKind.assessment to affected_kinds."
            )
