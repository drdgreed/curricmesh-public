"""
Seed script: load SOTA corpus into SotaSource rows.

SYNTHETIC / DEMO DATA ONLY — all corpus entries are fictional.
See seed/sota_corpus/README.md for details.

Idempotent: keyed on (title, captured_at). Entries that already exist are
skipped; existing rows are never updated or deleted.

Usage:
    cd backend
    python -m seed.load_sota        # via module
    python seed/load_sota.py        # direct

Prints a summary of rows by kind and planted-gap topic mention counts.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure app package is importable when run as a script from outside the backend dir.
_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.config import settings
from app.database import Base
from app.models import SotaSource  # noqa: F401 — registers all tables on Base.metadata

# ---------------------------------------------------------------------------
# Corpus location
# ---------------------------------------------------------------------------

_CORPUS_DIR = pathlib.Path(_here) / "sota_corpus"
_CORPUS_FILES = ["job_postings.json", "vendor_docs.json"]
_PLANTED_GAPS_FILE = _CORPUS_DIR / "PLANTED_GAPS.json"


# ---------------------------------------------------------------------------
# Core loader (sync-friendly: accepts an AsyncSession)
# ---------------------------------------------------------------------------


async def load_sota(session: AsyncSession) -> dict:
    """
    Load SOTA corpus entries into SotaSource rows.

    Idempotent: each entry is keyed on (title, captured_at). Entries that
    already exist (exact match on both fields) are skipped.

    Returns a summary dict with inserted/skipped counts by kind, and the
    planted-gap manifest.
    """
    # Load corpus files.
    raw_entries: list[dict] = []
    for filename in _CORPUS_FILES:
        path = _CORPUS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"SOTA corpus file missing: {path}")
        with open(path, encoding="utf-8") as fh:
            raw_entries.extend(json.load(fh))

    # Load planted-gaps manifest (for summary reporting).
    with open(_PLANTED_GAPS_FILE, encoding="utf-8") as fh:
        gaps_manifest = json.load(fh)

    inserted_by_kind: dict[str, int] = {}
    skipped_by_kind: dict[str, int] = {}

    for entry in raw_entries:
        title = entry["title"]
        kind = entry["kind"]
        body = entry.get("body")
        # Parse ISO date string → datetime with UTC timezone.
        captured_at_raw = entry["captured_at"]
        captured_at = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))

        # Idempotency check: skip if (title, captured_at) already exists.
        existing = await session.scalar(
            select(SotaSource).where(
                SotaSource.title == title,
                SotaSource.captured_at == captured_at,
            )
        )
        if existing is not None:
            skipped_by_kind[kind] = skipped_by_kind.get(kind, 0) + 1
            continue

        # NOTE: corpus entries carry a `topics[]` array for human readability,
        # but it is intentionally NOT persisted to the DB. The C2 gap researcher
        # discovers curriculum gaps by analyzing SotaSource.body text via the
        # LLM — letting the model find signals organically rather than reading
        # pre-labelled tags. The topics array and PLANTED_GAPS.json exist solely
        # as eval ground truth for the C4 harness.
        row = SotaSource(
            title=title,
            kind=kind,
            body=body,
            captured_at=captured_at,
        )
        session.add(row)
        inserted_by_kind[kind] = inserted_by_kind.get(kind, 0) + 1

    await session.commit()

    return {
        "total_corpus_entries": len(raw_entries),
        "inserted_by_kind": inserted_by_kind,
        "skipped_by_kind": skipped_by_kind,
        "total_inserted": sum(inserted_by_kind.values()),
        "total_skipped": sum(skipped_by_kind.values()),
        "planted_gaps": gaps_manifest.get("planted_gaps", []),
    }


# ---------------------------------------------------------------------------
# Standalone entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        summary = await load_sota(session)

    await engine.dispose()

    print("\n=== CurricMesh SOTA Corpus Load Summary ===")
    print(f"  Total corpus entries : {summary['total_corpus_entries']}")
    print(f"  Inserted             : {summary['total_inserted']}")
    print(f"  Skipped (duplicate)  : {summary['total_skipped']}")
    print()
    print("  Inserted by kind:")
    for kind, count in sorted(summary["inserted_by_kind"].items()):
        print(f"    {kind:<20} {count}")
    if summary["skipped_by_kind"]:
        print("  Skipped by kind:")
        for kind, count in sorted(summary["skipped_by_kind"].items()):
            print(f"    {kind:<20} {count}")
    print()
    total_entries = summary["total_corpus_entries"]
    print("  Planted-gap topics (ground truth for C4 eval):")
    for gap in summary["planted_gaps"]:
        print(
            f"    [{gap['gap_severity'].upper()}] {gap['topic']}"
            f" — {gap['mention_count']}/{total_entries} corpus entries"
            f" ({gap['mention_fraction']*100:.0f}%)"
        )
    print("===========================================\n")


if __name__ == "__main__":
    asyncio.run(_main())
