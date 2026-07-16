"""Freshness pipeline runner script.

Usage
-----
    python -m scripts.freshness_pipeline_run [--dry-run]

Iterates every organisation in the database, skips those with no active watch
items, and runs the freshness pipeline for the rest.

Gates
-----
- Must have ``FRESHNESS_PIPELINE_ENABLED=true`` in env (or pass ``--dry-run``).
- Must have ``ANTHROPIC_API_KEY`` set (always — dry-run reads still use the
  extractor for syllabus parsing; the key must be present).

Digest wiring (Task 8)
----------------------
``send_digest`` is called after every org run — success, all-fresh, AND
failure — from this script's org loop.  The digest is intentionally NOT
called from inside ``run_org`` because that function's failure path contains
a careful rollback → new-transaction dance and we must not introduce SMTP
latency or errors into that critical path.

Success / all-fresh path:
    After ``run_org`` returns, open a fresh ``org_scoped_session``, re-fetch
    the run by PK (the original session is already closed), call
    ``send_digest``, commit (persists ``digest_sent=True``).

Failure path:
    ``run_org`` commits the ``fail_run`` PipelineRun row then re-raises.
    The except block opens a fresh session, queries the latest
    ``status='failed'`` PipelineRun for the org, and calls ``send_digest``.

In both paths the digest call is wrapped in its own try/except so a digest
failure (e.g. SMTP down) never masks the run result.

dry_run:
    ``send_digest`` detects ``run.stats["dry_run"] is True`` and prints the
    digest to stdout instead of emailing; no ``digest_sent`` flag is set.
    A new org_scoped_session is still opened so pending-CCR counts are
    accurate in the preview (read-only; no commit needed).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

from app.config import settings
from app.database import async_admin_session, org_scoped_session
from app.freshness_pipeline.digest import send_digest
from app.freshness_pipeline.runner import run_org
from app.models.freshness_pipeline import PipelineRun, SourceWatchItem
from app.models.org import Organization

logger = logging.getLogger(__name__)


def _build_ai_client():
    """Build and return a real AIClient, or exit if ANTHROPIC_API_KEY is unset."""
    if not settings.ANTHROPIC_API_KEY:
        print(
            "ERROR: ANTHROPIC_API_KEY is not set. "
            "Cannot run the freshness pipeline (AI calls required for extraction).",
            file=sys.stderr,
        )
        sys.exit(1)
    from app.ai.client import AIClient

    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


async def _has_active_watch_items(session) -> bool:
    """Return True if the org has at least one active SourceWatchItem.

    Must be called with a session already scoped to the target org via
    ``org_scoped_session`` — SourceWatchItem is TenantScoped and
    RLS-filtered.  Both the app-layer ContextVar and the Postgres GUC must be
    live before this query runs (FORCE RLS on non-superuser role).
    """
    result = await session.execute(
        select(SourceWatchItem)
        .where(SourceWatchItem.active == True)  # noqa: E712
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _main(dry_run: bool) -> None:
    if not settings.FRESHNESS_PIPELINE_ENABLED and not dry_run:
        print(
            "ERROR: FRESHNESS_PIPELINE_ENABLED is not set to true. "
            "Pass --dry-run to run a read-only test without the gate.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = _build_ai_client()

    # Fetch all org IDs from the non-RLS organisations table.
    async with async_admin_session() as session:
        result = await session.execute(select(Organization.id))
        org_ids = [row[0] for row in result.all()]

    print(
        f"[freshness] Found {len(org_ids)} org(s). dry_run={dry_run}",
        flush=True,
    )

    for org_id in org_ids:
        # Skip orgs with no active watch items — org_scoped_session sets both
        # the ContextVar and the Postgres app.current_org GUC so the
        # RLS-filtered SourceWatchItem query returns the correct rows under a
        # non-superuser role (FORCE ROW LEVEL SECURITY).
        async with org_scoped_session(org_id) as probe_session:
            has_items = await _has_active_watch_items(probe_session)

        if not has_items:
            print(f"[freshness] Org {org_id}: no active watch items — skipping")
            continue

        print(f"[freshness] Org {org_id}: running …", flush=True)
        try:
            run = await run_org(
                lambda: org_scoped_session(org_id),
                org_id,
                extractor=client,
                searcher=client,
                enricher=client,
                judge=client,
                generator=client,
                dry_run=dry_run,
            )
            print(
                f"[freshness] Org {org_id}: status={run.status}  stats={run.stats}"
            )
            # Task 8: send success/all-fresh digest.  Wrapped so a digest
            # failure never masks the run result.
            try:
                async with org_scoped_session(org_id) as dsess:
                    if dry_run:
                        # Transient run (no DB row) — send_digest prints preview.
                        await send_digest(dsess, run)
                    else:
                        db_run = await dsess.get(PipelineRun, run.id)
                        if db_run is not None:
                            await send_digest(dsess, db_run)
                            await dsess.commit()
            except Exception as digest_exc:
                logger.warning(
                    "[freshness] Digest failed for org %s: %s",
                    org_id,
                    digest_exc,
                    exc_info=True,
                )
        except Exception as exc:
            # run_org already recorded the failure PipelineRun row and re-raised.
            print(
                f"[freshness] Org {org_id}: FAILED — {exc}",
                file=sys.stderr,
            )
            # Task 8: send failure digest.  Skip in dry-run — no run row was
            # committed, so there is nothing to query or report.
            if not dry_run:
                try:
                    async with org_scoped_session(org_id) as dsess:
                        fail_result = await dsess.execute(
                            select(PipelineRun)
                            .where(PipelineRun.status == "failed")
                            .order_by(PipelineRun.finished_at.desc())
                            .limit(1)
                        )
                        fail_run = fail_result.scalar_one_or_none()
                        if fail_run is not None:
                            await send_digest(dsess, fail_run)
                            await dsess.commit()
                except Exception as digest_exc:
                    logger.warning(
                        "[freshness] Failure digest failed for org %s: %s",
                        org_id,
                        digest_exc,
                        exc_info=True,
                    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the freshness pipeline for all organisations."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Read-only mode: fetch signals and diff syllabi but write nothing "
            "to the database. Does not require FRESHNESS_PIPELINE_ENABLED."
        ),
    )
    args = parser.parse_args()
    asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    main()
