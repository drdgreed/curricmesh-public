"""Seed a SyncTarget row for a curriculum in a target org.

Usage (from backend/):
    python -m scripts.seed_sync_target <org> <curriculum-name> <owner/repo> <path_prefix>

Examples:
    python -m scripts.seed_sync_target "Career Forge" "AI Engineering 101" \\
        my-org/agentic-ai-mastery content/lessons/

Idempotent: upserts by (curriculum_id, kind) — re-running updates the config
rather than duplicating rows.

Org resolution: UUID string → exact name match → case-insensitive substring.
Mirrors the pattern in scripts/seed_watchlist.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_admin_session, org_scoped_session
from app.models.curriculum import Curriculum
from app.models.sync import SyncTarget
from scripts.seed_watchlist import _resolve_org


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------


async def upsert_sync_target(
    session,
    curriculum_id,
    kind: str,
    repo: str,
    path_prefix: str,
) -> dict:
    """Upsert a SyncTarget by (curriculum_id, kind).

    Returns {"created": bool, "id": str}.
    """
    existing = await session.scalar(
        select(SyncTarget).where(
            SyncTarget.curriculum_id == curriculum_id,
            SyncTarget.kind == kind,
        )
    )
    config = {
        "repo": repo,
        "base_branch": "main",
        "path_prefix": path_prefix,
    }
    if existing is not None:
        existing.config = config
        existing.active = True
        await session.flush()
        return {"created": False, "id": str(existing.id)}

    target = SyncTarget(
        curriculum_id=curriculum_id,
        kind=kind,
        config=config,
        active=True,
    )
    session.add(target)
    await session.flush()
    return {"created": True, "id": str(target.id)}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _main(identifier: str, curriculum_name: str, repo: str, path_prefix: str) -> None:
    async with async_admin_session() as session:
        # organizations is deliberately NON-RLS (P-005) — safe under admin.
        org = await _resolve_org(session, identifier)

    # Curriculum IS RLS-scoped. In prod the DB owner is NOT a superuser and
    # FORCE RLS blocks it without the org GUC (AGENT_LESSONS P-011: the
    # false-zero trap) — so the curriculum MUST be resolved inside the
    # org-scoped session, never the admin one.
    async with org_scoped_session(org.id) as session:
        curriculum = await session.scalar(
            select(Curriculum).where(Curriculum.name == curriculum_name)
        )
        if curriculum is None:
            raise SystemExit(f"Curriculum not found: {curriculum_name!r}")
        curriculum_id = curriculum.id

        print(
            f"Seeding sync target into org={org.name!r} ({org.id}), "
            f"curriculum={curriculum_name!r} ({curriculum_id}), "
            f"repo={repo!r}, path_prefix={path_prefix!r}"
        )

        result = await upsert_sync_target(
            session,
            curriculum_id=curriculum_id,
            kind="github_pr",
            repo=repo,
            path_prefix=path_prefix,
        )
        await session.commit()

    action = "created" if result["created"] else "updated"
    print(f"Done — {action} SyncTarget id={result['id']}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(
            "Usage: python -m scripts.seed_sync_target"
            " <org> <curriculum-name> <owner/repo> <path_prefix>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
