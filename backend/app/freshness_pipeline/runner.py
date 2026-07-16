"""Freshness pipeline runner — orchestrates one org's end-to-end run.

Phase-2 step 5 design (judge-gated proposals):
    Findings are extracted directly from the extractor and filtered through two
    guards before a CCR is created:

    1. Open-CCR title guard (Phase-1 guard — kept as a cheap pre-filter):
       Any finding whose ``[AI] <topic>`` title matches an existing open draft
       CCR is silently dropped and ``ccrs_skipped_dup`` is incremented.  This
       guard protects against duplicates for CCRs created via /research, which
       calls ``analyze_gaps`` directly and is judge-agnostic in Phase 2.

    2. Judge gate (Phase-2):
       Each remaining finding is routed through ``route_finding`` (see
       ``app.freshness_pipeline.judging``).  Only ``adopt_now`` findings with
       confidence ≥ ``FRESHNESS_ADOPT_MIN_CONFIDENCE`` become CCRs; weaker
       signals accumulate in the ``gap_assessments`` monitor queue.

dry_run semantics:
    In dry_run mode ``run_org`` performs ALL reads but writes NOTHING to the
    database:

    - No ``PipelineRun`` row (the returned object is a transient Python value).
    - No ``SotaSource`` rows.
    - No CCRs, ``SotaFinding``, or ``GapAssessment`` rows (step 5 is skipped
      entirely).
    - No ``PipelineSeen`` rows.
    - No ``SyllabusSnapshot`` rows (``check_watch_item`` receives
      ``dry_run=True``).

    The runner script prints the returned stats dict to stdout.  The returned
    ``PipelineRun`` Python object is informational only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.enricher import _resolve_version, enrich_ccr
from app.ai.schemas import CorpusDoc, GapFinding
from app.ai.sota_researcher import _covered_topics
from app.config import settings
from app.core.actors import ensure_ai_researcher
from app.freshness_pipeline import industry as _industry
from app.freshness_pipeline import university as _university
from app.freshness_pipeline.content_cards import build_content_cards
from app.freshness_pipeline.generation import generate_change_set
from app.freshness_pipeline.judging import route_finding
from app.freshness_pipeline.syncing import sync_release
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.freshness_pipeline import PipelineSeen, PipelineRun, SourceWatchItem
from app.models.sota import SotaSource
from app.models.sync import SyncLog, SyncTarget
from app.models.workflow import ChangeRequest

logger = logging.getLogger(__name__)

# Bound per-run work: caps the detection-prompt size (each signal becomes a
# corpus doc of up to ~2000 chars) and the per-run AI cost. Surplus signals
# are NOT marked seen, so they drain across subsequent biweekly runs.
# (career-foundry's agent used 12; 25 gives the biweekly cadence more
# throughput while keeping the corpus well inside model context.)
MAX_SIGNALS_PER_RUN = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_existing_ai_titles(
    session: AsyncSession, curriculum_id: uuid.UUID
) -> frozenset[str]:
    """Return lowercased titles of open [AI] draft CCRs for a curriculum."""
    result = await session.execute(
        select(ChangeRequest.title).where(
            ChangeRequest.curriculum_id == curriculum_id,
            ChangeRequest.status == LifecycleStatus.draft,
            ChangeRequest.title.like("[AI] %"),
        )
    )
    return frozenset(row[0].lower() for row in result.all())


# ---------------------------------------------------------------------------
# run_org — public entry point
# ---------------------------------------------------------------------------


async def run_org(
    session_factory: Any,
    org_id: uuid.UUID,
    *,
    extractor: Any,
    searcher: Any,
    enricher: Any,
    judge: Any,
    generator: Any,
    dry_run: bool = False,
) -> PipelineRun:
    """Run the freshness pipeline for one organisation.

    Steps
    -----
    1. Open ``PipelineRun(status="running")`` under the org context.
    2. ``industry.fetch_all()`` + ``university.check_watch_item()`` per active
       watch item → all ``PipelineSignal``\\s.
    3. Filter vs ``PipelineSeen`` (per org) → new signals.
    4. New signals → ``SotaSource`` rows (global table — P-005).
    5. For each curriculum: resolve version, build corpus, call
       ``extractor.extract_gaps`` directly.  Per finding: (a) open-CCR title
       guard (Phase-1 guard, kept as a cheap pre-filter); (b) ``route_finding``
       through the judge — only gated ``adopt_now`` outcomes become CCRs.
    6. Auto-enrich adopted CCRs via ``enrich_ccr`` — per-CCR failures are
       logged into ``stats["errors"]`` and never abort the run.
    7. Mark ALL processed signals seen; set ``status="ok"``; commit.

    Any exception raised in steps 2-6 causes ``status="failed"``, seen-state
    is NOT advanced, the failure is recorded in a fresh DB transaction, and the
    exception is re-raised.

    Parameters
    ----------
    session_factory:
        Async context-manager factory called as
        ``async with session_factory() as session:``.  In production this must
        be ``lambda: org_scoped_session(org_id)`` so that both the app-layer
        ContextVar *and* the Postgres ``app.current_org`` GUC are set before
        any query runs (required under FORCE ROW LEVEL SECURITY on a
        non-superuser role — see AGENT_LESSONS P-001/P-011).  In tests an
        injected session that already carries the org context can be passed via
        the ``_make_factory`` shim.
    org_id:
        Organisation to process.
    extractor:
        Implements both ``GapExtractor`` and ``SyllabusExtractor`` protocols
        (``AIClient`` in production).
    searcher:
        ``WebSearcher`` protocol (used by the university adapter fallback).
    enricher:
        ``GapEnricher`` protocol.
    judge:
        ``GapJudge`` protocol (``AIClient`` in production).
    generator:
        ``ContentGenerator`` protocol (``AIClient`` in production).  Only
        called when ``settings.FRESHNESS_GENERATION_ENABLED`` is True.
    dry_run:
        If True, all reads run but nothing is written to the database. The
        returned ``PipelineRun`` is a transient Python object. See module
        docstring for full semantics.
    """
    async with session_factory() as session:
        # ------------------------------------------------------------------
        # Step 1: open run record.
        # ------------------------------------------------------------------
        run = PipelineRun(status="running")
        if not dry_run:
            session.add(run)
            await session.flush()

        stats: dict[str, Any] = {
            "dry_run": dry_run,
            "signals_fetched": 0,
            "new_signals": 0,
            "ccrs_created": 0,
            "ccrs_skipped_dup": 0,
            "search_only_items": 0,
            "gaps_judged": 0,
            "gaps_adopted": 0,
            "gaps_monitored": 0,
            "gaps_rejected": 0,
            "gaps_resurrected": 0,
            "gaps_strengthened": 0,
            "content_cards_built": 0,
            "content_cards_failed": 0,
            "changesets_generated": 0,
            "changesets_failed": 0,
            "syncs_attempted": 0,
            "syncs_succeeded": 0,
            "syncs_failed": 0,
            "syncs_skipped": 0,
            "errors": [],
        }

        try:
            # ----------------------------------------------------------
            # Sync sweep (Phase 4, kill-switched).
            # Retries any releases that have no SyncLog(status="success")
            # yet — e.g. the post-merge hook failed or the feature was
            # off at merge time. Runs BEFORE adapters so one run can both
            # sweep pending syncs and process new signals.
            # Write path: must stay inside the not-dry_run gate.
            # ----------------------------------------------------------
            if not dry_run and settings.FRESHNESS_SYNC_ENABLED and settings.SYNC_GITHUB_TOKEN:
                try:
                    targets_result = await session.execute(
                        select(SyncTarget).where(SyncTarget.active == True).order_by(SyncTarget.created_at)  # noqa: E712
                    )
                    targets = targets_result.scalars().all()

                    # Build ordered list of (Curriculum, CurriculumVersion, SyncTarget)
                    # for curricula whose active version has no success SyncLog.
                    pending: list[tuple] = []
                    for sync_target in targets:
                        cur = await session.get(Curriculum, sync_target.curriculum_id)
                        if cur is None or cur.active_content_version_id is None:
                            continue
                        cv_id = cur.active_content_version_id
                        already_synced = (
                            await session.execute(
                                select(SyncLog.id)
                                .where(
                                    SyncLog.curriculum_version_id == cv_id,
                                    # "skipped" (no mappable files) is terminal for
                                    # this version too — retrying every run would burn
                                    # a cap slot forever; delete the skipped SyncLog to
                                    # force a retry after fixing path config (review).
                                    SyncLog.status.in_(("success", "skipped")),
                                )
                                .limit(1)
                            )
                        ).first() is not None
                        if already_synced:
                            continue
                        cv = await session.get(CurriculumVersion, cv_id)
                        if cv is not None:
                            pending.append((cur, cv, sync_target))

                    for sweep_cur, sweep_cv, sweep_target in pending[:5]:
                        stats["syncs_attempted"] += 1
                        sync_log = await sync_release(
                            session,
                            curriculum=sweep_cur,
                            new_version=sweep_cv,
                            target=sweep_target,
                        )
                        if sync_log.status == "success":
                            stats["syncs_succeeded"] += 1
                        elif sync_log.status == "failed":
                            stats["syncs_failed"] += 1
                        elif sync_log.status == "skipped":
                            stats["syncs_skipped"] += 1

                except Exception as sweep_exc:
                    logger.warning(
                        "Sync sweep failed for org %s: %s",
                        org_id,
                        sweep_exc,
                        exc_info=True,
                    )

            # ----------------------------------------------------------
            # Step 2: Gather signals from all adapters.
            # ----------------------------------------------------------
            industry_signals = await _industry.fetch_all()

            active_items_result = await session.execute(
                select(SourceWatchItem).where(SourceWatchItem.active == True)  # noqa: E712
            )
            active_items = list(active_items_result.scalars().all())

            uni_signals: list = []
            async with httpx.AsyncClient(follow_redirects=True) as http:
                for item in active_items:
                    sigs = await _university.check_watch_item(
                        session,
                        item,
                        extractor=extractor,
                        searcher=searcher,
                        http=http,
                        dry_run=dry_run,
                    )
                    uni_signals.extend(sigs)

            all_signals = industry_signals + uni_signals
            stats["signals_fetched"] = len(all_signals)
            # Count watch-item-level search_only events by inspecting detail
            # text (best-effort: items that seed silently on first run are
            # not counted here even if they used the search fallback).
            stats["search_only_items"] = sum(
                1 for s in uni_signals if "fetch: search_only" in s.detail
            )

            # ----------------------------------------------------------
            # Step 3: Filter against the seen-state ledger.
            # ----------------------------------------------------------
            if all_signals:
                seen_result = await session.execute(
                    select(PipelineSeen.signal_id).where(
                        PipelineSeen.signal_id.in_(
                            [s.id for s in all_signals]
                        )
                    )
                )
                seen_ids: set[str] = {row[0] for row in seen_result.all()}
            else:
                seen_ids = set()

            new_signals = [s for s in all_signals if s.id not in seen_ids]
            stats["new_signals"] = len(new_signals)

            # ----------------------------------------------------------
            # Step 3b: First-run seeding + per-run cap (the career-foundry
            # lessons, surfaced by the 2026-07-05 dry-run: 1,258 industry
            # backlog items on a fresh ledger).
            #
            # * FIRST RUN (org has NO seen-state at all): the entire feed
            #   history is backlog, not news. Mark everything seen, process
            #   nothing — never treat the backlog as new developments.
            # * Later runs: cap the batch at MAX_SIGNALS_PER_RUN to bound
            #   the detection prompt size and per-run cost. Uncapped
            #   surplus is NOT marked seen, so it drains over later runs.
            # ----------------------------------------------------------
            has_any_seen = (
                await session.execute(select(PipelineSeen.id).limit(1))
            ).first() is not None

            if new_signals and not has_any_seen:
                stats["first_run_seeded"] = len(new_signals)
                logger.info(
                    "First run for org %s: seeding %d signals as seen, "
                    "no processing.",
                    org_id,
                    len(new_signals),
                )
                if not dry_run:
                    for sig in new_signals:
                        session.add(PipelineSeen(signal_id=sig.id))
                    await session.flush()
                new_signals = []
                stats["new_signals"] = 0
            elif len(new_signals) > MAX_SIGNALS_PER_RUN:
                stats["signals_deferred"] = len(new_signals) - MAX_SIGNALS_PER_RUN
                logger.info(
                    "Capping run at %d signals (%d deferred to later runs).",
                    MAX_SIGNALS_PER_RUN,
                    stats["signals_deferred"],
                )
                new_signals = new_signals[:MAX_SIGNALS_PER_RUN]
                stats["new_signals"] = len(new_signals)

            # ----------------------------------------------------------
            # Step 4: Persist SotaSource rows for new signals.
            # Global table — no org context required for the insert.
            # ----------------------------------------------------------
            if not dry_run and new_signals:
                for sig in new_signals:
                    session.add(
                        SotaSource(
                            title=sig.title,
                            kind=sig.source_kind,
                            body=sig.detail,
                        )
                    )
                await session.flush()

            # ----------------------------------------------------------
            # Step 5: Per-curriculum gap detection + judge-gated proposal.
            # Skipped entirely when nothing new arrived: the corpus is
            # unchanged, so re-running detection would only re-produce
            # findings that are already assessed — pure API cost.
            # ----------------------------------------------------------
            if not dry_run and new_signals:
                # Build corpus: ALL SotaSource rows (curated + newly added).
                corpus_result = await session.execute(select(SotaSource))
                full_corpus = list(corpus_result.scalars().all())

                curricula_result = await session.execute(select(Curriculum))
                curricula = list(curricula_result.scalars().all())

                # ensure_ai_researcher is called once per org run (idempotent
                # get-or-create; calling it per curriculum would be wasteful).
                ai_user = await ensure_ai_researcher(session)
                run_date = datetime.now(tz=timezone.utc).date().isoformat()

                for curriculum in curricula:
                    version = await _resolve_version(session, curriculum)
                    if version is None:
                        logger.debug(
                            "Skipping curriculum %s: no resolvable version",
                            curriculum.id,
                        )
                        continue

                    # Build topical surface and corpus docs (same pattern as
                    # analyze_gaps in sota_researcher).
                    covered_topics = await _covered_topics(session, version)
                    corpus_docs = [
                        CorpusDoc(title=s.title, kind=s.kind, body=s.body or "")
                        for s in full_corpus
                    ]

                    # Build content cards for content-aware detection (Phase 3).
                    # Failures are contained: cards stays None and the run
                    # degrades to Phase-2 (topic-only) detection.
                    cards = None
                    try:
                        cards = await build_content_cards(session, curriculum)
                    except Exception as cards_exc:
                        logger.warning(
                            "Content card build failed for curriculum %s: %s",
                            curriculum.id,
                            cards_exc,
                        )
                        stats["content_cards_failed"] += 1
                    stats["content_cards_built"] += len(cards or [])

                    # Let extractor errors propagate — the failure path
                    # handles them cleanly (rollback + PipelineRun failed).
                    findings = await extractor.extract_gaps(
                        covered_topics, corpus_docs, covered_content=cards
                    )

                    # Phase-1 open-CCR title guard (cheap pre-filter).
                    # Fetched once per curriculum, before the finding loop,
                    # to protect against /research-created CCRs that exist
                    # outside the judge's assessment memory.
                    existing_titles = await _fetch_existing_ai_titles(
                        session, curriculum.id
                    )

                    adopted_ccrs = []
                    for finding in findings:
                        key = f"[AI] {finding.topic}".lower()
                        if key in existing_titles:
                            logger.debug(
                                "Dedup guard: skipping finding %r "
                                "(existing draft [AI] CCR)",
                                finding.topic,
                            )
                            stats["ccrs_skipped_dup"] += 1
                            continue

                        outcome = await route_finding(
                            session,
                            curriculum_id=curriculum.id,
                            finding=finding,
                            judge=judge,
                            covered_topics=covered_topics,
                            run_date=run_date,
                            author_id=ai_user.id,
                        )

                        # "skipped" outcomes (already-promoted, or reject below
                        # its resurrection threshold) make NO AI call — they are
                        # routed, not judged. Counting them would break the digest
                        # arithmetic (judged = adopted+monitored+rejected) (T5 review).
                        if outcome.action != "skipped":
                            stats["gaps_judged"] += 1
                        if outcome.action == "adopted":
                            stats["gaps_adopted"] += 1
                            stats["ccrs_created"] += 1
                            if outcome.ccr is not None:
                                adopted_ccrs.append(outcome.ccr)
                        elif outcome.action == "monitored":
                            stats["gaps_monitored"] += 1
                        elif outcome.action == "rejected":
                            stats["gaps_rejected"] += 1
                        # "skipped" (already-promoted) counts nothing extra.

                        if outcome.resurrected:
                            stats["gaps_resurrected"] += 1
                        if outcome.strengthened:
                            stats["gaps_strengthened"] += 1

                    # -----------------------------------------------
                    # Step 6: Auto-enrich ONLY adopted CCRs.
                    # Failures are advisory — log and continue.
                    # -----------------------------------------------
                    for ccr in adopted_ccrs:
                        try:
                            await enrich_ccr(
                                session, ccr_id=ccr.id, enricher=enricher
                            )
                        except Exception as enrich_exc:
                            logger.warning(
                                "Enrichment failed for CCR %s: %s",
                                ccr.id,
                                enrich_exc,
                            )
                            stats["errors"].append(
                                f"enrich_ccr({ccr.id}): {enrich_exc}"
                            )

                    # -----------------------------------------------
                    # Step 6b: Change-set generation (kill-switched).
                    # Only runs when FRESHNESS_GENERATION_ENABLED is True
                    # AND there are adopted CCRs — enrichment MUST run
                    # first because generation reads impact.enrichment.
                    # Failures are per-CCR contained; never fail the run.
                    # -----------------------------------------------
                    if settings.FRESHNESS_GENERATION_ENABLED and adopted_ccrs:
                        for ccr in adopted_ccrs:
                            try:
                                cs = await generate_change_set(
                                    session, ccr=ccr, generator=generator
                                )
                                if cs is not None:
                                    stats["changesets_generated"] += 1
                                else:
                                    stats["changesets_failed"] += 1
                            except Exception as gen_exc:
                                logger.warning(
                                    "Change-set generation failed for CCR %s: %s",
                                    ccr.id,
                                    gen_exc,
                                )
                                stats["changesets_failed"] += 1

            # ----------------------------------------------------------
            # Step 7: Mark seen; finalize run record; commit.
            # ----------------------------------------------------------
            now = datetime.now(tz=timezone.utc)
            run.status = "ok"
            run.stats = stats
            run.finished_at = now

            if not dry_run:
                for sig in new_signals:
                    session.add(PipelineSeen(signal_id=sig.id))
                session.add(run)
                await session.flush()
                await session.commit()

            return run

        except Exception as exc:
            logger.error(
                "Pipeline run failed for org %s: %s", org_id, exc, exc_info=True
            )
            if not dry_run:
                # Roll back all partial writes, then record the failure in
                # a fresh implicit transaction.  Seen-state is NOT advanced.
                # NOTE: we commit the fail_run row HERE and then re-raise; the
                # org_scoped_session factory's except-path rollback then runs
                # against an already-committed (clean) session — a harmless
                # no-op, so the committed failure record survives (T7 review).
                try:
                    await session.rollback()
                    stats["errors"].append(str(exc))
                    fail_run = PipelineRun(
                        status="failed",
                        finished_at=datetime.now(tz=timezone.utc),
                        stats=stats,
                    )
                    session.add(fail_run)
                    await session.flush()
                    await session.commit()
                except Exception as record_exc:
                    logger.error(
                        "Failed to record run failure for org %s: %s",
                        org_id,
                        record_exc,
                    )
            raise
