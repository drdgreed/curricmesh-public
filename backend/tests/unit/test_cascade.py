"""Unit tests for app/core/cascade/engine.py — pure functions, no DB.

Task B1 TDD: write tests first, confirm they fail, then implement.

Edge direction convention:
    DependencyEdge(from_asset_id=A, to_asset_id=B)  means B depends on A.
    Changing A cascades to B (A is upstream; B is the dependent).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.cascade.engine import (
    Misalignment,
    ProposedBump,
    alignment_report,
    cascade,
)
from app.core.versioning.semver import BumpType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge(from_id: uuid.UUID, to_id: uuid.UUID) -> SimpleNamespace:
    """Build a minimal edge object compatible with the pure cascade functions."""
    return SimpleNamespace(from_asset_id=from_id, to_asset_id=to_id)


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# Stable UUIDs for readable test assertions
LO = uuid.UUID("00000000-0000-0000-0000-000000000001")
ASSESSMENT = uuid.UUID("00000000-0000-0000-0000-000000000002")
RUBRIC = uuid.UUID("00000000-0000-0000-0000-000000000003")
A = uuid.UUID("00000000-0000-0000-0000-000000000010")
B = uuid.UUID("00000000-0000-0000-0000-000000000011")
C = uuid.UUID("00000000-0000-0000-0000-000000000012")
D = uuid.UUID("00000000-0000-0000-0000-000000000013")


# ---------------------------------------------------------------------------
# ProposedBump dataclass
# ---------------------------------------------------------------------------


def test_proposed_bump_defaults_to_minor():
    """cascade() returns minor bumps by default per framework rule 2.1."""
    pb = ProposedBump(asset_id=ASSESSMENT, reason="upstream changed")
    assert pb.bump_type == BumpType.minor


def test_proposed_bump_can_override_bump_type():
    pb = ProposedBump(asset_id=ASSESSMENT, reason="major change", bump_type=BumpType.major)
    assert pb.bump_type == BumpType.major


# ---------------------------------------------------------------------------
# cascade — core path: LO → assessment → rubric
# ---------------------------------------------------------------------------


def test_cascade_lo_to_assessment_to_rubric():
    """cascade(LO, edges) proposes bumps for assessment and rubric, not LO itself."""
    edges = [
        _edge(LO, ASSESSMENT),        # ASSESSMENT depends on LO
        _edge(ASSESSMENT, RUBRIC),    # RUBRIC depends on ASSESSMENT
    ]
    result = cascade(LO, edges)

    bumped_ids = {pb.asset_id for pb in result}
    assert bumped_ids == {ASSESSMENT, RUBRIC}

    # LO (the start asset) must NOT appear
    assert LO not in bumped_ids


def test_cascade_does_not_include_start_asset():
    """cascade never proposes a bump for the asset that triggered the cascade."""
    edges = [_edge(LO, ASSESSMENT)]
    result = cascade(LO, edges)
    assert all(pb.asset_id != LO for pb in result)


def test_cascade_all_bumps_are_minor_by_default():
    """All proposed bumps default to BumpType.minor (framework rule 2.1)."""
    edges = [_edge(LO, ASSESSMENT), _edge(ASSESSMENT, RUBRIC)]
    result = cascade(LO, edges)
    assert all(pb.bump_type == BumpType.minor for pb in result)


# ---------------------------------------------------------------------------
# cascade — leaf node
# ---------------------------------------------------------------------------


def test_cascade_from_leaf_returns_empty():
    """A leaf node (no outgoing edges) cascades to nothing."""
    edges = [_edge(LO, ASSESSMENT)]
    result = cascade(RUBRIC, edges)  # RUBRIC has no outgoing edges in this set
    assert result == []


def test_cascade_from_isolated_node_returns_empty():
    """An asset with no edges at all cascades to nothing."""
    result = cascade(LO, [])
    assert result == []


# ---------------------------------------------------------------------------
# cascade — cycle safety (hard requirement)
# ---------------------------------------------------------------------------


def test_cascade_is_acyclic_safe():
    """A cycle A→B, B→A must not cause infinite recursion.

    cascade(A) should terminate and return B exactly once (not A).
    """
    edges = [
        _edge(A, B),
        _edge(B, A),
    ]
    result = cascade(A, edges)
    bumped_ids = {pb.asset_id for pb in result}

    # Must terminate (no RecursionError/loop) and return B once, not A
    assert bumped_ids == {B}
    # No duplicate entries
    assert len(result) == len(bumped_ids)


def test_cascade_longer_cycle_terminates():
    """A→B→C→A cycle starting from A terminates with B and C exactly once."""
    edges = [
        _edge(A, B),
        _edge(B, C),
        _edge(C, A),
    ]
    result = cascade(A, edges)
    bumped_ids = {pb.asset_id for pb in result}

    assert bumped_ids == {B, C}
    assert len(result) == 2


# ---------------------------------------------------------------------------
# cascade — diamond dedup
# ---------------------------------------------------------------------------


def test_cascade_diamond_deduplicated():
    """Diamond: A→B, A→C, B→D, C→D — D must appear only once in the result.

    This verifies the visited-set de-duplication for converging paths.
    """
    edges = [
        _edge(A, B),
        _edge(A, C),
        _edge(B, D),
        _edge(C, D),
    ]
    result = cascade(A, edges)
    bumped_ids = [pb.asset_id for pb in result]

    # D must appear exactly once
    assert bumped_ids.count(D) == 1
    # B and C should be present
    assert B in bumped_ids
    assert C in bumped_ids
    # A (start) must not appear
    assert A not in bumped_ids


# ---------------------------------------------------------------------------
# alignment_report
# ---------------------------------------------------------------------------


def test_alignment_report_flags_stale_dependent():
    """Edge A→B; B's latest change predates A's → Misalignment(dependent=B, dependency=A)."""
    edges = [_edge(A, B)]
    latest_change_at = {
        A: _dt(2024, 6, 1),   # A updated more recently
        B: _dt(2024, 1, 1),   # B is stale
    }
    result = alignment_report(edges, latest_change_at)

    assert len(result) == 1
    m = result[0]
    assert m.dependent_asset_id == B
    assert m.dependency_asset_id == A
    assert isinstance(m.reason, str) and len(m.reason) > 0


def test_alignment_report_no_misalignment_when_dependent_is_newer():
    """B updated after A → no misalignment."""
    edges = [_edge(A, B)]
    latest_change_at = {
        A: _dt(2024, 1, 1),
        B: _dt(2024, 6, 1),   # B is up to date
    }
    result = alignment_report(edges, latest_change_at)
    assert result == []


def test_alignment_report_no_misalignment_when_same_date():
    """B updated at exactly the same time as A → not stale, no misalignment."""
    edges = [_edge(A, B)]
    ts = _dt(2024, 3, 15)
    latest_change_at = {A: ts, B: ts}
    result = alignment_report(edges, latest_change_at)
    assert result == []


def test_alignment_report_multiple_edges_independent():
    """Each edge is evaluated independently; both can be stale simultaneously."""
    # A→B (B stale), A→C (C fresh)
    edges = [_edge(A, B), _edge(A, C)]
    latest_change_at = {
        A: _dt(2024, 6, 1),
        B: _dt(2024, 1, 1),   # stale
        C: _dt(2024, 9, 1),   # fresh
    }
    result = alignment_report(edges, latest_change_at)
    assert len(result) == 1
    assert result[0].dependent_asset_id == B


def test_alignment_report_missing_asset_skipped():
    """If an asset has no entry in latest_change_at, that edge is skipped gracefully."""
    edges = [_edge(A, B)]
    # B is missing from the map — should not raise, should skip this edge
    latest_change_at = {A: _dt(2024, 6, 1)}
    result = alignment_report(edges, latest_change_at)
    # No crash; skips the edge silently and returns an empty list
    assert result == []


# ---------------------------------------------------------------------------
# Misalignment dataclass
# ---------------------------------------------------------------------------


def test_misalignment_fields():
    m = Misalignment(
        dependent_asset_id=B,
        dependency_asset_id=A,
        reason="B predates A",
    )
    assert m.dependent_asset_id == B
    assert m.dependency_asset_id == A
    assert m.reason == "B predates A"
