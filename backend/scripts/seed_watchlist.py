"""Seed the 4 validated university watchlist items into a target org.

Usage (from backend/):
    python -m scripts.seed_watchlist <org-name-or-uuid>

Idempotent: re-running does not duplicate rows (upsert-by-label).

Org resolution: UUID string → exact name match → case-insensitive substring.
Mirrors the pattern in seed/load_agentic_mastery.py (session.scalar +
Organization query) and seed/bootcamp_curriculum.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

# Make the `app` package importable when run as `python -m scripts.seed_watchlist`
# from backend/ (the -m flag puts the package dir on sys.path, but the parent
# backend/ must be on it too so `app.*` imports resolve).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_admin_session, org_scoped_session
from app.models.freshness_pipeline import SourceWatchItem
from app.models.org import Organization

# ---------------------------------------------------------------------------
# Seed data — verbatim from the plan contract.
# MIT uses the deep /syllabus URL (de-risk finding: landing page is content-thin).
# ---------------------------------------------------------------------------

WATCHLIST: list[tuple[str, str, str, str]] = [
    (
        "UC Berkeley",
        "CS294 Agentic AI (F25)",
        "https://rdi.berkeley.edu/agentic-ai/f25",
        "Berkeley CS294 agentic AI syllabus",
    ),
    (
        "Stanford",
        "CS336 Language Modeling from Scratch",
        "https://cs336.stanford.edu/",
        "Stanford CS336 syllabus",
    ),
    (
        "MIT",
        "6.8610 Advanced NLP",
        "https://mit-6861.github.io/syllabus",
        "MIT 6.8610 advanced NLP syllabus",
    ),
    (
        "CMU",
        "11-711 Advanced NLP",
        "https://cmu-l3.github.io/anlp-spring2026/",
        "CMU 11-711 advanced NLP syllabus",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_org(session: AsyncSession, identifier: str) -> Organization:
    """Resolve an org by UUID, exact name, or case-insensitive name substring."""
    # 1. Try UUID.
    try:
        org_id = uuid.UUID(identifier)
        org = await session.scalar(select(Organization).where(Organization.id == org_id))
        if org is not None:
            return org
    except ValueError:
        pass

    # 2. Exact name match.
    org = await session.scalar(
        select(Organization).where(Organization.name == identifier)
    )
    if org is not None:
        return org

    # 3. Case-insensitive substring.
    result = await session.execute(select(Organization))
    all_orgs = result.scalars().all()
    matches = [o for o in all_orgs if identifier.lower() in o.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(o.name for o in matches)
        raise SystemExit(f"Ambiguous org identifier {identifier!r} — matches: {names}")
    raise SystemExit(f"Org not found: {identifier!r}")


async def upsert_watchlist(session: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """Upsert the 4 validated watch items under *org_id*.

    Idempotent: calling twice with the same session produces 4 rows, not 8.
    Returns {"created": N, "skipped": M}.

    Must be called with a session already scoped to *org_id* via
    ``org_scoped_session`` — SourceWatchItem is TenantScoped and
    RLS-filtered.  Both the app-layer ContextVar and the Postgres GUC must be
    live before any query runs (FORCE ROW LEVEL SECURITY on non-superuser role).
    """
    created = 0
    skipped = 0

    for institution, label, url, search_hint in WATCHLIST:
        existing = await session.scalar(
            select(SourceWatchItem).where(SourceWatchItem.label == label)
        )
        if existing is not None:
            skipped += 1
            continue

        session.add(
            SourceWatchItem(
                label=label,
                institution=institution,
                url=url,
                search_hint=search_hint,
                active=True,
            )
        )
        created += 1

    if created:
        await session.flush()

    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _main(identifier: str) -> None:
    # Resolve the org — organizations is not RLS-scoped, so a bare admin
    # session is fine for this read.
    async with async_admin_session() as session:
        org = await _resolve_org(session, identifier)

    print(f"Seeding watchlist into org: {org.name!r} ({org.id})")

    # Write the watch items under a fully-scoped session: org_scoped_session
    # sets both the app-layer ContextVar and the Postgres app.current_org GUC
    # so the RLS-filtered SourceWatchItem inserts succeed under a non-superuser
    # DB role (FORCE ROW LEVEL SECURITY — see AGENT_LESSONS P-001/P-011).
    async with org_scoped_session(org.id) as session:
        counts = await upsert_watchlist(session, org.id)
        await session.commit()

    print(f"Done — created={counts['created']}, skipped={counts['skipped']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m scripts.seed_watchlist <org-name-or-uuid>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_main(sys.argv[1]))
