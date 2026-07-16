"""Workflow engine for CurricMesh — Task A7.

Exposes:
  rules   — pure business-rule guards (no DB)
  engine  — async DB-backed orchestration
"""

from app.core.workflow.rules import (
    QA_DIMENSIONS,
    WorkflowError,
    assert_lo_change_includes_assessment,
    assert_patch_only_mid_cohort,
    assert_qa_complete,
    qa_verdict_valid,
)
from app.core.workflow.engine import (
    can_release,
    record_approval,
    record_qa,
    release_ccr,
    submit_ccr,
)

__all__ = [
    "QA_DIMENSIONS",
    "WorkflowError",
    "assert_lo_change_includes_assessment",
    "assert_patch_only_mid_cohort",
    "assert_qa_complete",
    "qa_verdict_valid",
    "can_release",
    "record_approval",
    "record_qa",
    "release_ccr",
    "submit_ccr",
]
