"""Freshness pipeline email digest.

Placement decision (Task 8):
    The digest is sent from the org loop in
    ``scripts/freshness_pipeline_run.py``, NOT from inside
    ``app.freshness_pipeline.runner.run_org``.

    ``run_org``'s failure path contains a careful rollback → new-transaction
    dance to record the failed ``PipelineRun``.  Inserting digest sending into
    that path risks (a) holding the DB session open during a synchronous SMTP
    call (latency spike under connection-pool pressure), and (b) an SMTP error
    masking the fact that the failure record was committed successfully.

    Script-side seam keeps the concerns cleanly separate:

    - **Success / all-fresh**: open a fresh ``org_scoped_session``, re-fetch the
      run by PK (the original session is already closed), call ``send_digest``,
      commit (persists ``digest_sent=True``).
    - **Failure**: ``run_org``'s except path commits the ``fail_run`` row and
      re-raises.  The script's except block opens a fresh session, queries the
      latest ``status='failed'`` ``PipelineRun`` for the org, calls
      ``send_digest``, commits.
    - Any digest error is caught and logged; the run result is unaffected.
    - **dry_run**: ``send_digest`` detects ``run.stats["dry_run"] is True`` and
      prints subject + body to stdout instead of emailing; ``digest_sent`` is
      never set.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.email import send_email
from app.models.enums import LifecycleStatus
from app.models.freshness_pipeline import PipelineRun
from app.models.workflow import ChangeRequest

logger = logging.getLogger(__name__)

_AI_INBOX_URL = "/ai-inbox"


# ---------------------------------------------------------------------------
# build_digest — pure function, no I/O
# ---------------------------------------------------------------------------


def build_digest(
    run: PipelineRun,
    pending_ai_ccrs: list,
    *,
    org_label: str = "",
) -> tuple[str, str]:
    """Build the subject and body for the freshness-run digest email.

    Pure function — no I/O.  The caller is responsible for querying
    ``pending_ai_ccrs`` and for sending the resulting email.

    Three shapes, selected from ``run.status`` and ``run.stats``:

    - **failed** (``run.status == "failed"``): error summary + run ID.
    - **all_fresh** (``status == "ok"`` and ``stats["new_signals"] == 0``):
      one-line "nothing new" note.
    - **normal** (``status == "ok"`` and ``stats["new_signals"] > 0``): ACTIONS
      TAKEN section (from stats) + ACTIONS NEEDED section listing ALL pending
      AI CCRs with an ``/ai-inbox`` pointer.

    Parameters
    ----------
    run:
        The completed ``PipelineRun``.  May be transient (no DB row) in
        dry_run mode — only ``run.status``, ``run.stats``, ``run.finished_at``,
        ``run.started_at``, and ``run.id`` are read.
    pending_ai_ccrs:
        All draft ``[AI] `` ``ChangeRequest`` rows for the org at digest time.
        Only ``len()`` and ``.title`` are accessed — the list may contain
        transient objects (unit tests) or DB-attached instances.
    org_label:
        Optional human-readable org name for the body header (unused in
        Phase 1; reserved for per-org email extension).
    """
    ts = run.finished_at or (run.started_at if hasattr(run, "started_at") else None)
    if ts is None:
        ts = datetime.now(tz=timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")

    stats = run.stats or {}

    # ------------------------------------------------------------------
    # Failed shape
    # ------------------------------------------------------------------
    if run.status == "failed":
        subject = f"[CurricMesh] Freshness run FAILED — {date_str}"
        errors = stats.get("errors", [])
        error_msg = errors[-1] if errors else "Unknown error"
        run_id = str(run.id) if getattr(run, "id", None) is not None else "unknown"
        body = f"Error: {error_msg}\nRun ID: {run_id}"
        return subject, body

    # ------------------------------------------------------------------
    # All-fresh shape
    # ------------------------------------------------------------------
    new_signals = stats.get("new_signals", 0)
    if new_signals == 0:
        signals_fetched = stats.get("signals_fetched", 0)
        first_run_seeded = stats.get("first_run_seeded", 0)
        if first_run_seeded:
            # First run ever: the backlog was seeded as already-seen, not
            # treated as news. Say so — a bare "all fresh" would mislead.
            subject = f"[CurricMesh] Freshness run {date_str} — first run, ledger seeded"
            body = (
                f"First run: seeded {first_run_seeded} backlog signal(s) into the "
                f"seen-ledger; no proposals from a first run by design. The next "
                f"run reports genuinely new developments. No action needed."
            )
            return subject, body
        subject = f"[CurricMesh] Freshness run {date_str} — all fresh"
        body = f"Checked {signals_fetched} source(s), nothing new; no action needed."
        return subject, body

    # ------------------------------------------------------------------
    # Normal shape
    # ------------------------------------------------------------------
    pending_count = len(pending_ai_ccrs)
    noun = "proposal" if pending_count == 1 else "proposals"
    subject = (
        f"[CurricMesh] Freshness run {date_str} — {pending_count} {noun} await review"
    )

    signals_fetched = stats.get("signals_fetched", 0)
    ccrs_created = stats.get("ccrs_created", 0)
    ccrs_skipped_dup = stats.get("ccrs_skipped_dup", 0)
    search_only_items = stats.get("search_only_items", 0)
    errors = stats.get("errors", [])

    lines: list[str] = []
    lines.append("ACTIONS TAKEN")
    fetch_line = f"  - {signals_fetched} signal(s) fetched ({new_signals} new)"
    if search_only_items:
        fetch_line += f"; {search_only_items} search-only"
    lines.append(fetch_line)
    lines.append(f"  - {ccrs_created} CCR(s) created + enriched")
    if "gaps_judged" in stats:
        lines.append(
            f"  - {stats.get('gaps_judged', 0)} gap(s) judged: "
            f"{stats.get('gaps_adopted', 0)} promoted, "
            f"{stats.get('gaps_monitored', 0)} monitoring "
            f"({stats.get('gaps_strengthened', 0)} strengthened), "
            f"{stats.get('gaps_rejected', 0)} rejected, "
            f"{stats.get('gaps_resurrected', 0)} re-reviewed"
        )
    if "changesets_generated" in stats:
        lines.append(
            f"  - {stats.get('changesets_generated', 0)} change-set(s) generated "
            f"({stats.get('changesets_failed', 0)} degraded to proposal-only)"
        )
    if "syncs_attempted" in stats and stats["syncs_attempted"]:
        syncs_attempted = stats["syncs_attempted"]
        syncs_succeeded = stats.get("syncs_succeeded", 0)
        lines.append(f"  - {syncs_succeeded}/{syncs_attempted} release sync(s) → PR")
    if ccrs_skipped_dup:
        lines.append(
            f"  - {ccrs_skipped_dup} finding(s) skipped (open CCR already exists)"
        )
    if errors:
        lines.append(
            f"  - {len(errors)} error(s): {'; '.join(str(e) for e in errors[:3])}"
        )
    lines.append("")
    lines.append("ACTIONS NEEDED")
    lines.append(
        f"  - {pending_count} {noun} await your review in the AI Inbox: {_AI_INBOX_URL}"
    )
    for ccr in pending_ai_ccrs:
        lines.append(f"    • {ccr.title}")
    syncs_failed = stats.get("syncs_failed", 0)
    if syncs_failed:
        lines.append("  - release sync failing — check SyncLog")

    body = "\n".join(lines)
    return subject, body


# ---------------------------------------------------------------------------
# send_digest — async, queries DB and sends (or previews) the email
# ---------------------------------------------------------------------------


async def send_digest(session: AsyncSession, run: PipelineRun) -> bool:
    """Send (or preview) the freshness-run digest email.

    Queries all draft ``[AI] `` ``ChangeRequest`` rows from the org-scoped
    *session*, builds the digest via :func:`build_digest`, then:

    - **dry_run** (``run.stats["dry_run"] is True``): prints subject + body to
      stdout; does not send; does not set ``digest_sent``.  Returns ``False``.
    - **SMTP not configured** (``SMTP_HOST`` or ``NOTIFY_EMAIL_TO`` empty):
      skips the send; returns ``False``.
    - **SMTP configured**: calls ``send_email`` via ``run_in_executor``
      (smtplib is synchronous — mirroring ``app.integrations.notifier``);
      sets ``run.digest_sent = True`` and flushes (caller must commit);
      returns ``True``.

    Parameters
    ----------
    session:
        Org-scoped async session.  Must be scoped to the correct org so that
        ``ChangeRequest`` (TenantScoped) returns only that org's CCRs.  The
        *run* object must be attached to this session when SMTP is configured
        so that the ``digest_sent`` flush is visible to the caller's commit.
    run:
        The ``PipelineRun`` to digest.  Must be attached to *session* for the
        non-dry-run path; may be transient in dry_run mode.
    """
    # Query all pending [AI] draft CCRs in this org.
    result = await session.execute(
        select(ChangeRequest).where(
            ChangeRequest.status == LifecycleStatus.draft,
            ChangeRequest.title.like("[AI] %"),
        )
    )
    pending_ai_ccrs = list(result.scalars().all())

    subject, body = build_digest(run, pending_ai_ccrs)

    # dry_run: print preview to stdout, no email, no digest_sent.
    is_dry = bool((run.stats or {}).get("dry_run"))
    if is_dry:
        print(f"[digest dry-run]\nSubject: {subject}\n\n{body}", flush=True)
        return False

    # Unconfigured: no-op.
    if not settings.SMTP_HOST or not settings.NOTIFY_EMAIL_TO:
        logger.debug(
            "Digest not sent for run %s: SMTP not configured (SMTP_HOST=%r, NOTIFY_EMAIL_TO=%r)",
            getattr(run, "id", "?"),
            settings.SMTP_HOST,
            settings.NOTIFY_EMAIL_TO,
        )
        return False

    # Send via executor (smtplib is synchronous).
    send_fn = functools.partial(
        send_email,
        settings.SMTP_HOST,
        settings.SMTP_PORT,
        settings.SMTP_USER,
        settings.SMTP_PASSWORD,
        settings.NOTIFY_EMAIL_TO,
        subject,
        body,
        from_email=settings.FROM_EMAIL,
    )
    await asyncio.get_running_loop().run_in_executor(None, send_fn)

    # Mark sent on the attached run object; caller commits.
    run.digest_sent = True
    await session.flush()
    return True
