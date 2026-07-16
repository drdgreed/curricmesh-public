"""Unit tests for app/core/diff/service.py — pure functions, no DB.

Task B4 TDD: tests written first, confirmed failing, then implemented.

JSON shape assumptions (documented once here):
  Rubric:  {"criteria": [{"name": str, "weight": float}, ...]}
           Names are the unique key for matching criteria across versions.
  Learning-objectives: [{"id": str, "text": str}, ...]
           IDs are the unique key for matching items across versions.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.diff.service import (
    DiffResult,
    StructuredDiff,
    TextDiff,
    diff,
    lo_diff,
    rubric_diff,
    text_diff,
)


# ---------------------------------------------------------------------------
# Helpers — build lightweight AssetVersion-like objects without a DB session
# ---------------------------------------------------------------------------


def _av(asset_id: uuid.UUID, body_ref: str | None, kind: str = "lesson_plan") -> SimpleNamespace:
    """Minimal AssetVersion stand-in for pure-function tests."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        asset_id=asset_id,
        body_ref=body_ref,
        # expose the asset's kind through a nested asset object
        asset=SimpleNamespace(kind=SimpleNamespace(value=kind)),
    )


ASSET_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSET_B = uuid.UUID("00000000-0000-0000-0000-000000000002")


# ---------------------------------------------------------------------------
# text_diff — unified line diff
# ---------------------------------------------------------------------------


def test_markdown_unified_diff():
    """text_diff detects a changed line and populates added, removed, and unified."""
    a = "line1\nline2\n"
    b = "line1\nline2 changed\n"

    result = text_diff(a, b)

    assert isinstance(result, TextDiff)
    # The removed list should contain the old line
    assert any("line2" in line for line in result.removed)
    # The added list should contain the new line
    assert any("line2 changed" in line for line in result.added)
    # unified string should contain diff markers
    assert "+" in result.unified
    assert "-" in result.unified


def test_text_diff_identical_content_empty():
    """Identical content → empty diff (no added, removed; unified is blank/minimal)."""
    result = text_diff("same\n", "same\n")

    assert result.added == []
    assert result.removed == []
    # Unified may have header lines but no +/- content lines
    content_lines = [
        ln for ln in result.unified.splitlines()
        if ln.startswith("+") or ln.startswith("-")
    ]
    assert content_lines == []


def test_text_diff_empty_inputs():
    """Both sides empty → empty diff, no crash."""
    result = text_diff("", "")
    assert result.added == []
    assert result.removed == []


def test_text_diff_one_side_empty():
    """From empty to non-empty → all lines added."""
    result = text_diff("", "hello\nworld\n")
    assert len(result.added) == 2
    assert result.removed == []


# ---------------------------------------------------------------------------
# rubric_diff — semantic diff of rubric JSON
# ---------------------------------------------------------------------------


def test_rubric_json_semantic_diff():
    """Weight change + one add + one remove is captured correctly."""
    rubric_a = {
        "criteria": [
            {"name": "clarity", "weight": 0.2},
            {"name": "depth", "weight": 0.5},
            {"name": "removed_crit", "weight": 0.3},
        ]
    }
    rubric_b = {
        "criteria": [
            {"name": "clarity", "weight": 0.3},   # weight change 0.2 → 0.3
            {"name": "depth", "weight": 0.5},      # unchanged
            {"name": "added_crit", "weight": 0.2}, # new criterion
            # removed_crit is gone
        ]
    }

    result = rubric_diff(rubric_a, rubric_b)

    assert isinstance(result, StructuredDiff)

    # Weight change on 'clarity'
    changed_keys = [c["key"] for c in result.changed]
    assert "clarity" in changed_keys
    clarity_change = next(c for c in result.changed if c["key"] == "clarity")
    assert abs(clarity_change["from"] - 0.2) < 1e-9
    assert abs(clarity_change["to"] - 0.3) < 1e-9

    # 'depth' unchanged — must NOT appear in changed
    assert "depth" not in changed_keys

    # added_crit is new
    added_names = [c["name"] for c in result.added]
    assert "added_crit" in added_names

    # removed_crit is gone
    removed_names = [c["name"] for c in result.removed]
    assert "removed_crit" in removed_names


def test_rubric_diff_identical_empty():
    """Identical rubrics → no changes, adds, or removes."""
    rubric = {"criteria": [{"name": "clarity", "weight": 0.5}]}
    result = rubric_diff(rubric, rubric)

    assert result.added == []
    assert result.removed == []
    assert result.changed == []


# ---------------------------------------------------------------------------
# lo_diff — diff of learning-objectives list
# ---------------------------------------------------------------------------


def test_lo_json_diff():
    """Added and removed LO items are reported correctly."""
    lo_a = [
        {"id": "lo-1", "text": "Understand recursion"},
        {"id": "lo-2", "text": "Apply sorting algorithms"},
        {"id": "lo-removed", "text": "This will be gone"},
    ]
    lo_b = [
        {"id": "lo-1", "text": "Understand recursion"},
        {"id": "lo-2", "text": "Apply sorting algorithms"},
        {"id": "lo-added", "text": "Brand new objective"},
        # lo-removed is gone
    ]

    result = lo_diff(lo_a, lo_b)

    assert isinstance(result, StructuredDiff)

    added_ids = [item["id"] for item in result.added]
    assert "lo-added" in added_ids

    removed_ids = [item["id"] for item in result.removed]
    assert "lo-removed" in removed_ids

    # Unchanged items must not appear
    assert "lo-1" not in added_ids
    assert "lo-1" not in removed_ids


def test_lo_diff_text_change_reported_in_changed():
    """When an LO's text changes (same id), it should appear in changed."""
    lo_a = [{"id": "lo-1", "text": "Old text"}]
    lo_b = [{"id": "lo-1", "text": "New text"}]

    result = lo_diff(lo_a, lo_b)

    changed_keys = [c["key"] for c in result.changed]
    assert "lo-1" in changed_keys


def test_lo_diff_identical_empty():
    """Identical LO lists → no changes, adds, or removes."""
    lo = [{"id": "lo-1", "text": "Same text"}]
    result = lo_diff(lo, lo)

    assert result.added == []
    assert result.removed == []
    assert result.changed == []


# ---------------------------------------------------------------------------
# diff() dispatcher — cross-asset rejection and kind dispatch
# ---------------------------------------------------------------------------


def test_diff_rejects_cross_asset():
    """diff() raises ValueError when the two AssetVersions belong to different assets."""
    av1 = _av(ASSET_A, "content a")
    av2 = _av(ASSET_B, "content b")

    with pytest.raises(ValueError, match=r"(?i)asset"):
        diff(av1, av2)


def test_diff_text_kinds_use_text_diff():
    """lesson_plan (text kind) routes through text_diff and populates DiffResult.text."""
    av1 = _av(ASSET_A, "line1\nline2\n", kind="lesson_plan")
    av2 = _av(ASSET_A, "line1\nline2 updated\n", kind="lesson_plan")

    result = diff(av1, av2)

    assert isinstance(result, DiffResult)
    assert result.kind == "lesson_plan"
    assert result.text is not None
    assert result.structured is None


def test_diff_rubric_kind_uses_structured_diff():
    """rubric kind routes through rubric_diff and populates DiffResult.structured."""
    import json

    rubric_a = json.dumps({"criteria": [{"name": "clarity", "weight": 0.2}]})
    rubric_b = json.dumps({"criteria": [{"name": "clarity", "weight": 0.4}]})

    av1 = _av(ASSET_A, rubric_a, kind="rubric")
    av2 = _av(ASSET_A, rubric_b, kind="rubric")

    result = diff(av1, av2)

    assert isinstance(result, DiffResult)
    assert result.kind == "rubric"
    assert result.structured is not None
    assert result.text is None


def test_diff_lo_kind_uses_structured_diff():
    """learning_objectives kind routes through lo_diff and populates DiffResult.structured."""
    import json

    lo_a = json.dumps([{"id": "lo-1", "text": "Understand loops"}])
    lo_b = json.dumps([{"id": "lo-1", "text": "Understand loops"}, {"id": "lo-2", "text": "New"}])

    av1 = _av(ASSET_A, lo_a, kind="learning_objectives")
    av2 = _av(ASSET_A, lo_b, kind="learning_objectives")

    result = diff(av1, av2)

    assert result.kind == "learning_objectives"
    assert result.structured is not None
    assert result.text is None


def test_diff_missing_body_ref_treated_as_empty():
    """None body_ref should be treated as empty string — no crash."""
    av1 = _av(ASSET_A, None, kind="lesson_plan")
    av2 = _av(ASSET_A, "some content\n", kind="lesson_plan")

    result = diff(av1, av2)

    assert result.text is not None
    assert len(result.text.added) > 0
