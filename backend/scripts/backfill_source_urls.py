"""One-time prod script: backfill LineageAsset.source_url for AI Engineering 101 lessons.

Reads the curriculum.json key→file mapping, then sets source_url on any
lesson-plan LineageAsset where it is NULL or stale.  Idempotent.

Usage (from backend/):
    python -m scripts.backfill_source_urls <org-name-or-uuid>

Org resolution: UUID string → exact name match → case-insensitive substring.
Mirrors the pattern in scripts/seed_watchlist.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_admin_session, org_scoped_session
from scripts.seed_watchlist import _resolve_org
from seed.load_agentic_mastery import _load, backfill_lesson_source_urls


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _main(identifier: str) -> None:
    # Resolve the org using the shared helper from seed_watchlist / seed_agentic.
    # organizations is not RLS-scoped, so a bare admin session is fine.
    async with async_admin_session() as session:
        org = await _resolve_org(session, identifier)

    print(f"Backfilling source_urls into org: {org.name!r} ({org.id})")

    curriculum_json = _load("curriculum.json")

    # Write the LineageAsset updates under a fully-scoped session so the RLS
    # FORCE policy passes (same pattern as seed_watchlist.py).
    async with org_scoped_session(org.id) as session:
        counts = await backfill_lesson_source_urls(session, curriculum_json)
        await session.commit()

    print(f"Done — set={counts['set']}, skipped={counts['skipped']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m scripts.backfill_source_urls <org-name-or-uuid>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_main(sys.argv[1]))
