"""
Unit tests for the SOTA corpus loader (seed/load_sota.py).

Coverage:
1. Loader inserts the expected number of SotaSource rows by kind.
2. PLANTED_GAPS.json parses correctly and contains ≥2 gap topics
   with positive mention counts.
3. Re-running the loader is idempotent — no duplicate rows inserted.
"""

import json
import pathlib

import pytest
from sqlalchemy import func, select

from app.models.sota import SotaSource
from seed.load_sota import load_sota

# ---------------------------------------------------------------------------
# Expected corpus sizes (must stay in sync with the JSON files).
# ---------------------------------------------------------------------------

_EXPECTED_JOB_POSTINGS = 20
_EXPECTED_VENDOR_DOCS = 20
_EXPECTED_TOTAL = _EXPECTED_JOB_POSTINGS + _EXPECTED_VENDOR_DOCS

_CORPUS_DIR = pathlib.Path(__file__).parent.parent.parent / "seed" / "sota_corpus"
_PLANTED_GAPS_PATH = _CORPUS_DIR / "PLANTED_GAPS.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_inserts_expected_row_counts(db_session):
    """Loader creates the correct number of SotaSource rows for each kind."""
    summary = await load_sota(db_session)

    # Summary dict integrity.
    assert summary["total_inserted"] == _EXPECTED_TOTAL
    assert summary["total_skipped"] == 0

    # Verify against the actual DB rows.
    job_count = await db_session.scalar(
        select(func.count(SotaSource.id)).where(SotaSource.kind == "job_posting")
    )
    vendor_count = await db_session.scalar(
        select(func.count(SotaSource.id)).where(SotaSource.kind == "vendor_doc")
    )
    assert job_count == _EXPECTED_JOB_POSTINGS, (
        f"Expected {_EXPECTED_JOB_POSTINGS} job_posting rows, got {job_count}"
    )
    assert vendor_count == _EXPECTED_VENDOR_DOCS, (
        f"Expected {_EXPECTED_VENDOR_DOCS} vendor_doc rows, got {vendor_count}"
    )

    # By-kind breakdown in summary matches DB.
    assert summary["inserted_by_kind"]["job_posting"] == job_count
    assert summary["inserted_by_kind"]["vendor_doc"] == vendor_count


@pytest.mark.asyncio
async def test_planted_gaps_manifest_is_valid():
    """PLANTED_GAPS.json is parseable and contains ≥2 gap topics with positive counts."""
    assert _PLANTED_GAPS_PATH.exists(), f"PLANTED_GAPS.json not found at {_PLANTED_GAPS_PATH}"

    with open(_PLANTED_GAPS_PATH, encoding="utf-8") as fh:
        manifest = json.load(fh)

    gaps = manifest.get("planted_gaps", [])

    # Must have at least 2 planted gaps.
    assert len(gaps) >= 2, f"Expected ≥2 planted gaps, got {len(gaps)}"

    corpus_size = manifest["_meta"]["corpus_size"]

    for gap in gaps:
        # Each gap must have a topic name.
        assert isinstance(gap.get("topic"), str) and gap["topic"], (
            f"Gap entry missing 'topic': {gap}"
        )
        # Each gap must have a positive mention count.
        count = gap.get("mention_count", 0)
        assert isinstance(count, int) and count > 0, (
            f"Gap '{gap.get('topic')}' has non-positive mention_count: {count}"
        )

    # At least one gap must be a strong-signal gap (≥ 90% of corpus entries).
    # This confirms that high-confidence planted gaps exist without requiring
    # ALL gaps to clear 50% — intentional varied signal strength is realistic
    # and tests the eval harness at multiple detection difficulty levels.
    fractions = [gap["mention_count"] / corpus_size for gap in gaps]
    assert any(f >= 0.9 for f in fractions), (
        f"Expected at least one planted gap with mention_fraction >= 0.90 "
        f"(strong signal), but got fractions: {[f'{f:.0%}' for f in fractions]}"
    )


@pytest.mark.asyncio
async def test_loader_is_idempotent(db_session):
    """Running the loader twice does not duplicate rows."""
    # First run.
    summary1 = await load_sota(db_session)
    assert summary1["total_inserted"] == _EXPECTED_TOTAL
    assert summary1["total_skipped"] == 0

    # Second run — same session, same DB state.
    summary2 = await load_sota(db_session)
    assert summary2["total_inserted"] == 0, (
        f"Second loader run inserted {summary2['total_inserted']} rows (expected 0)"
    )
    assert summary2["total_skipped"] == _EXPECTED_TOTAL

    # DB row count is unchanged.
    total_in_db = await db_session.scalar(select(func.count(SotaSource.id)))
    assert total_in_db == _EXPECTED_TOTAL, (
        f"Expected {_EXPECTED_TOTAL} total rows after two runs, got {total_in_db}"
    )
