"""Self-tests + golden-equivalence tests for the Task-G baseline harness.

What this proves:
  1. **Determinism** — capturing the same seeded curriculum twice yields an
     identical golden (no UUID / timestamp / ordering leakage).
  2. **Committed-fixture match** — the live capture equals the JSON fixture
     committed under ``fixtures/`` (the baseline the M2 ports will assert against
     stays in sync with reality).
  3. **Comparator correctness** — ``assert_equivalent`` passes on current-vs-current
     (even when list order differs) and *fails* on an injected difference.

The DB-backed tests skip cleanly when the seeded DB isn't present (see
``conftest.require_seeded``), so the comparator self-test (3) always runs.

To (re)generate the committed fixtures after an intentional behavior change::

    REGEN_GOLDEN=1 ./venv/bin/python -m pytest \
        tests/golden/test_golden_harness.py::test_capture_matches_committed_fixture -q
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tests.golden import capture
from tests.golden.capture import GoldenMismatch, assert_equivalent
from tests.golden.conftest import SEEDED_CURRICULA, org_scoped_session

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_path(slug: str) -> Path:
    return FIXTURES_DIR / f"{slug}.json"


def _load_fixture(slug: str) -> dict:
    return json.loads(_fixture_path(slug).read_text())


def _dump_fixture(slug: str, data: dict) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _fixture_path(slug).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# (1) + (2): DB-backed determinism + committed-fixture equivalence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(SEEDED_CURRICULA))
async def test_capture_is_deterministic(slug, golden_engine, require_seeded):
    """Capturing the same curriculum twice yields byte-identical goldens."""
    org_id = require_seeded[slug]
    async with org_scoped_session(golden_engine, org_id) as s1:
        first = await capture.capture_all(s1, slug)
    async with org_scoped_session(golden_engine, org_id) as s2:
        second = await capture.capture_all(s2, slug)

    # Identical as raw structures...
    assert first == second
    # ...and the comparator agrees.
    assert_equivalent(first, second)


@pytest.mark.parametrize("slug", sorted(SEEDED_CURRICULA))
async def test_capture_matches_committed_fixture(slug, golden_engine, require_seeded):
    """Live capture equals the committed golden fixture (REGEN_GOLDEN=1 to rewrite)."""
    org_id = require_seeded[slug]
    async with org_scoped_session(golden_engine, org_id) as s:
        actual = await capture.capture_all(s, slug)

    if os.environ.get("REGEN_GOLDEN") == "1":
        _dump_fixture(slug, actual)
        pytest.skip(f"regenerated fixture {slug}.json")

    expected = _load_fixture(slug)
    # Strong equality (the capture is already normalized + sorted)...
    assert actual == expected
    # ...and the order-insensitive comparator the M2 ports use also passes.
    assert_equivalent(expected, actual)


# ---------------------------------------------------------------------------
# (3): Comparator self-test — runs with NO DB (uses committed fixtures as data)
# ---------------------------------------------------------------------------


def _any_committed_fixture() -> dict:
    """Load a committed fixture as static comparator-test data (no DB needed)."""
    paths = sorted(FIXTURES_DIR.glob("*.json"))
    if not paths:
        pytest.skip("no committed fixtures yet — generate them first")
    return json.loads(paths[0].read_text())


def test_assert_equivalent_passes_current_vs_current():
    """current-vs-current is equivalent, even after shuffling list order."""
    golden = _any_committed_fixture()

    shuffled = copy.deepcopy(golden)
    # Reverse every list in the structure to prove order-insensitivity.
    _reverse_all_lists(shuffled)

    # Sanity: we actually perturbed ordering somewhere.
    assert shuffled != golden or _has_no_multi_item_list(golden)
    assert_equivalent(golden, shuffled)


def test_assert_equivalent_fails_on_injected_difference():
    """An injected value change is detected and reported with a path."""
    golden = _any_committed_fixture()
    mutated = copy.deepcopy(golden)
    _inject_difference(mutated)

    with pytest.raises(GoldenMismatch):
        assert_equivalent(golden, mutated)


def test_assert_equivalent_fails_on_added_member():
    """A spurious extra node (structural addition) is detected."""
    golden = _any_committed_fixture()
    mutated = copy.deepcopy(golden)
    mutated["graph"]["nodes"].append(
        {
            "key": "zzz/injected/node",
            "kind": "lab",
            "label": "Injected",
            "latest_version": "9.9.9",
            "status": "active",
            "misaligned": False,
        }
    )
    with pytest.raises(GoldenMismatch):
        assert_equivalent(golden, mutated)


# ---------------------------------------------------------------------------
# helpers for the comparator self-test
# ---------------------------------------------------------------------------


def _reverse_all_lists(obj) -> None:
    """In-place reverse every list found in a nested dict/list structure."""
    if isinstance(obj, dict):
        for v in obj.values():
            _reverse_all_lists(v)
    elif isinstance(obj, list):
        obj.reverse()
        for v in obj:
            _reverse_all_lists(v)


def _has_no_multi_item_list(obj) -> bool:
    """True if there is no list with >=2 items anywhere (perturbation no-op case)."""
    if isinstance(obj, dict):
        return all(_has_no_multi_item_list(v) for v in obj.values())
    if isinstance(obj, list):
        if len(obj) >= 2:
            return False
        return all(_has_no_multi_item_list(v) for v in obj)
    return True


def _inject_difference(obj) -> bool:
    """Mutate the first scalar found to a sentinel; return True once done."""
    if isinstance(obj, dict):
        for k in sorted(obj):
            v = obj[k]
            if isinstance(v, str):
                obj[k] = v + "__INJECTED__"
                return True
            if _inject_difference(v):
                return True
    elif isinstance(obj, list):
        for v in obj:
            if _inject_difference(v):
                return True
    return False
