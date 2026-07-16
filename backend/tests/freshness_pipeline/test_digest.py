"""Tests for app.freshness_pipeline.digest.

Pure ``build_digest`` tests are synchronous — they exercise the three email
shapes without touching the database or SMTP.

``send_digest`` integration tests use ``db_session`` (full schema with RLS)
and monkeypatch ``app.freshness_pipeline.digest.send_email`` to capture the
outbound call without opening a real SMTP connection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshness_pipeline.digest import build_digest, send_digest
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.freshness_pipeline import PipelineRun
from app.models.workflow import ChangeRequest
from tests.conftest import DEFAULT_ORG_ID  # noqa: F401 — kept for clarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    status: str,
    stats: dict | None = None,
    finished_at: datetime | None = None,
) -> PipelineRun:
    """Build a transient PipelineRun (no DB row)."""
    r = PipelineRun(
        status=status,
        stats=stats,
        finished_at=finished_at or datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
    )
    return r


def _ok_stats(
    *,
    new_signals: int = 3,
    signals_fetched: int = 10,
    ccrs_created: int = 2,
    ccrs_skipped_dup: int = 0,
    search_only_items: int = 0,
    errors: list | None = None,
) -> dict:
    return {
        "signals_fetched": signals_fetched,
        "new_signals": new_signals,
        "ccrs_created": ccrs_created,
        "ccrs_skipped_dup": ccrs_skipped_dup,
        "search_only_items": search_only_items,
        "errors": errors or [],
    }


def _ccr(title: str) -> ChangeRequest:
    """Build a transient ChangeRequest (no DB row — only .title is accessed)."""
    return ChangeRequest(title=title, status=LifecycleStatus.draft)


# ---------------------------------------------------------------------------
# Test 1: normal shape — subject + body structure
# ---------------------------------------------------------------------------


def test_normal_shape_subject_contains_count_date_and_label():
    run = _run("ok", _ok_stats(new_signals=3))
    ccrs = [_ccr("[AI] foo topic"), _ccr("[AI] bar topic"), _ccr("[AI] baz topic")]
    subject, body = build_digest(run, ccrs)

    assert "[CurricMesh]" in subject
    assert "2026-07-15" in subject
    assert "3 proposals" in subject
    assert "await review" in subject


def test_normal_shape_body_has_actions_taken_and_needed_sections():
    run = _run(
        "ok",
        _ok_stats(
            new_signals=3,
            signals_fetched=10,
            ccrs_created=2,
            ccrs_skipped_dup=1,
        ),
    )
    ccrs = [_ccr("[AI] alpha"), _ccr("[AI] beta"), _ccr("[AI] gamma")]
    subject, body = build_digest(run, ccrs)

    assert "ACTIONS TAKEN" in body
    assert "ACTIONS NEEDED" in body
    # AI-inbox pointer
    assert "/ai-inbox" in body
    # CCR titles listed
    assert "[AI] alpha" in body
    assert "[AI] beta" in body
    # Signals fetched count
    assert "10 signal(s)" in body
    # Skipped-dup notice
    assert "1 finding(s) skipped" in body


def test_normal_shape_single_proposal_uses_singular_noun():
    run = _run("ok", _ok_stats(new_signals=1))
    ccrs = [_ccr("[AI] singleton")]
    subject, _ = build_digest(run, ccrs)
    assert "1 proposal await" in subject


def test_normal_shape_search_only_items_appear_in_actions_taken():
    run = _run("ok", _ok_stats(new_signals=2, signals_fetched=20, search_only_items=1))
    ccrs = [_ccr("[AI] A"), _ccr("[AI] B")]
    _, body = build_digest(run, ccrs)
    assert "1 search-only" in body


def test_normal_shape_errors_appear_in_actions_taken():
    run = _run(
        "ok",
        _ok_stats(new_signals=1, errors=["enrich_ccr(abc): timeout"]),
    )
    ccrs = [_ccr("[AI] topic")]
    _, body = build_digest(run, ccrs)
    assert "1 error(s)" in body
    assert "enrich_ccr" in body


# ---------------------------------------------------------------------------
# Test 2: all-fresh shape
# ---------------------------------------------------------------------------


def test_all_fresh_subject_and_body():
    run = _run("ok", _ok_stats(new_signals=0, signals_fetched=15))
    subject, body = build_digest(run, [])

    assert "all fresh" in subject
    assert "2026-07-15" in subject
    assert "nothing new" in body.lower()
    assert "no action needed" in body.lower()
    assert "15 source(s)" in body


def test_all_fresh_has_no_actions_sections():
    run = _run("ok", _ok_stats(new_signals=0))
    _, body = build_digest(run, [])

    assert "ACTIONS TAKEN" not in body
    assert "ACTIONS NEEDED" not in body


def test_all_fresh_ignores_pending_ccrs_parameter():
    """Even if stale CCRs exist they do not appear in the all-fresh body."""
    run = _run("ok", _ok_stats(new_signals=0, signals_fetched=5))
    ccrs = [_ccr("[AI] lingering")]
    _, body = build_digest(run, ccrs)

    assert "[AI] lingering" not in body


# ---------------------------------------------------------------------------
# Test 3: failed shape
# ---------------------------------------------------------------------------


def test_failed_shape_subject_contains_failed_and_date():
    run = _run("failed", {"errors": ["DB connection refused"], "signals_fetched": 0})
    subject, body = build_digest(run, [])

    assert "FAILED" in subject
    assert "2026-07-15" in subject
    assert "DB connection refused" in body


def test_failed_shape_includes_run_id_in_body():
    fixed_id = uuid.uuid4()
    run = _run("failed", {"errors": ["timeout"]})
    run.id = fixed_id
    _, body = build_digest(run, [])

    assert str(fixed_id) in body


def test_failed_shape_no_errors_falls_back_to_unknown():
    run = _run("failed", {})
    _, body = build_digest(run, [])

    assert "Unknown error" in body


def test_failed_shape_never_shows_actions_sections():
    run = _run("failed", {"errors": ["oops"]})
    _, body = build_digest(run, [])

    assert "ACTIONS TAKEN" not in body
    assert "ACTIONS NEEDED" not in body


# ---------------------------------------------------------------------------
# Test 4: unconfigured SMTP → False, no send_email call, no digest_sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_notify_email_returns_false_no_send(
    db_session: AsyncSession, monkeypatch
):
    """Empty NOTIFY_EMAIL_TO: send_digest returns False; send_email never invoked."""
    send_calls: list = []

    def _fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))

    monkeypatch.setattr("app.freshness_pipeline.digest.send_email", _fake_send)
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_HOST", "smtp.test")
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.NOTIFY_EMAIL_TO", "")

    run = PipelineRun(
        status="ok",
        finished_at=datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        stats=_ok_stats(new_signals=2),
    )
    db_session.add(run)
    await db_session.flush()

    result = await send_digest(db_session, run)

    assert result is False
    assert send_calls == [], "send_email must NOT be called when NOTIFY_EMAIL_TO is empty"
    assert run.digest_sent is False


@pytest.mark.asyncio
async def test_unconfigured_smtp_host_returns_false_no_send(
    db_session: AsyncSession, monkeypatch
):
    """Empty SMTP_HOST: same no-op behavior as empty NOTIFY_EMAIL_TO."""
    send_calls: list = []

    def _fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))

    monkeypatch.setattr("app.freshness_pipeline.digest.send_email", _fake_send)
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_HOST", "")
    monkeypatch.setattr(
        "app.freshness_pipeline.digest.settings.NOTIFY_EMAIL_TO",
        "test@example.com",
    )

    run = PipelineRun(
        status="ok",
        finished_at=datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        stats=_ok_stats(new_signals=1),
    )
    db_session.add(run)
    await db_session.flush()

    result = await send_digest(db_session, run)

    assert result is False
    assert send_calls == []
    assert run.digest_sent is False


# ---------------------------------------------------------------------------
# Test 5: pending count includes PRE-EXISTING draft [AI] CCRs (not just this run's)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_count_includes_preexisting_ai_ccrs(
    db_session: AsyncSession, monkeypatch
):
    """A draft [AI] CCR seeded before this run must appear in subject + body.

    This proves send_digest queries ALL open [AI] CCRs from the DB, not just
    ones created in the current run.
    """
    # Curriculum FK required by ChangeRequest.
    cur = Curriculum(
        name="Test Curriculum",
        slug=f"digest-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(cur)
    await db_session.flush()

    # Pre-existing CCR from a prior run.
    pre_ccr = ChangeRequest(
        curriculum_id=cur.id,
        title="[AI] Pre-existing Gap",
        status=LifecycleStatus.draft,
    )
    db_session.add(pre_ccr)
    await db_session.flush()

    # Capture send_email calls.
    send_calls: list = []

    def _fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))

    monkeypatch.setattr("app.freshness_pipeline.digest.send_email", _fake_send)
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_HOST", "smtp.test")
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_PORT", 587)
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_USER", "resend")
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        "app.freshness_pipeline.digest.settings.NOTIFY_EMAIL_TO",
        "david@example.com",
    )
    monkeypatch.setattr(
        "app.freshness_pipeline.digest.settings.FROM_EMAIL",
        "noreply@curricmesh.com",
    )

    # This run created 0 CCRs itself but the pre-existing one is still open.
    run = PipelineRun(
        status="ok",
        finished_at=datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        stats=_ok_stats(new_signals=1, ccrs_created=0),
    )
    db_session.add(run)
    await db_session.flush()

    result = await send_digest(db_session, run)

    assert result is True
    assert run.digest_sent is True
    assert len(send_calls) == 1, "send_email must be called exactly once"

    # Unpack positional args from the captured call.
    # send_email(host, port, user, password, to, subject, body, *, from_email)
    call_args, call_kwargs = send_calls[0]
    subject_sent = call_args[5]
    body_sent = call_args[6]

    # Subject must reflect the one pre-existing pending CCR.
    assert "1 proposal" in subject_sent, f"Expected '1 proposal' in subject: {subject_sent!r}"
    # Body must list the pre-existing CCR title.
    assert "[AI] Pre-existing Gap" in body_sent
    assert "/ai-inbox" in body_sent
    # from_email kwarg passed through.
    assert call_kwargs.get("from_email") == "noreply@curricmesh.com"


# ---------------------------------------------------------------------------
# Additional: dry_run prints and returns False without emailing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_prints_preview_and_returns_false(
    db_session: AsyncSession, monkeypatch, capsys
):
    """dry_run mode: prints to stdout, does not call send_email, returns False."""
    send_calls: list = []

    def _fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))

    monkeypatch.setattr("app.freshness_pipeline.digest.send_email", _fake_send)
    monkeypatch.setattr("app.freshness_pipeline.digest.settings.SMTP_HOST", "smtp.test")
    monkeypatch.setattr(
        "app.freshness_pipeline.digest.settings.NOTIFY_EMAIL_TO",
        "david@example.com",
    )

    # Transient run with dry_run flag — not persisted.
    run = PipelineRun(
        status="ok",
        finished_at=datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        stats={**_ok_stats(new_signals=2), "dry_run": True},
    )

    result = await send_digest(db_session, run)

    assert result is False
    assert send_calls == [], "send_email must NOT be called in dry_run mode"
    captured = capsys.readouterr()
    assert "[digest dry-run]" in captured.out
    assert "Subject:" in captured.out


# ---------------------------------------------------------------------------
# Task 6: judge-stats line in normal shape
# ---------------------------------------------------------------------------


def test_normal_shape_with_judge_stats_renders_judge_line():
    """Normal digest with judge stats includes the judgments line."""
    stats = {
        **_ok_stats(new_signals=5, ccrs_created=3),
        "gaps_judged": 4,
        "gaps_adopted": 3,
        "gaps_monitored": 1,
        "gaps_strengthened": 1,
        "gaps_rejected": 0,
        "gaps_resurrected": 1,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] A"), _ccr("[AI] B"), _ccr("[AI] C")]
    _, body = build_digest(run, ccrs)

    assert "4 gap(s) judged" in body
    assert "3 promoted" in body
    assert "1 monitoring" in body
    assert "1 strengthened" in body
    assert "0 rejected" in body
    assert "1 re-reviewed" in body


def test_normal_shape_without_judge_keys_omits_judge_line():
    """Old-style stats dict (no gaps_judged key) renders without the judge line."""
    run = _run("ok", _ok_stats(new_signals=3, ccrs_created=2))
    ccrs = [_ccr("[AI] alpha"), _ccr("[AI] beta")]
    _, body = build_digest(run, ccrs)

    assert "judged" not in body
    assert "re-reviewed" not in body
    # Existing ACTIONS TAKEN content still present.
    assert "ACTIONS TAKEN" in body
    assert "ACTIONS NEEDED" in body


def test_all_fresh_and_first_run_shapes_never_contain_judge_line():
    """Early-return shapes (all-fresh, first-run) never emit the judge line
    even if judge keys are present in stats."""
    judge_stats = {
        "gaps_judged": 2,
        "gaps_adopted": 1,
        "gaps_monitored": 0,
        "gaps_strengthened": 0,
        "gaps_rejected": 1,
        "gaps_resurrected": 0,
    }

    # All-fresh shape (new_signals == 0).
    run_fresh = _run("ok", {**_ok_stats(new_signals=0, signals_fetched=10), **judge_stats})
    _, body_fresh = build_digest(run_fresh, [])
    assert "judged" not in body_fresh

    # First-run shape.
    run_first = _run(
        "ok",
        {
            "signals_fetched": 100,
            "new_signals": 0,
            "first_run_seeded": 100,
            "ccrs_created": 0,
            "ccrs_skipped_dup": 0,
            "errors": [],
            **judge_stats,
        },
    )
    _, body_first = build_digest(run_first, [])
    assert "judged" not in body_first


def test_build_digest_first_run_shape():
    """First-run stats produce the seeded-ledger shape, not a misleading all-fresh."""
    run = PipelineRun(
        status="ok",
        stats={"signals_fetched": 1258, "new_signals": 0, "first_run_seeded": 1258,
               "ccrs_created": 0, "ccrs_skipped_dup": 0, "errors": []},
    )
    subject, body = build_digest(run, [])
    assert "first run, ledger seeded" in subject
    assert "1258" in body
    assert "No action needed" in body


# ---------------------------------------------------------------------------
# Phase-4 sync stats lines in normal digest shape
# ---------------------------------------------------------------------------


def test_sync_line_renders_when_syncs_attempted():
    """Normal digest with syncs_attempted > 0 includes the sync → PR line."""
    stats = {
        **_ok_stats(new_signals=2, ccrs_created=1),
        "syncs_attempted": 1,
        "syncs_succeeded": 1,
        "syncs_failed": 0,
        "syncs_skipped": 0,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] topic")]
    _, body = build_digest(run, ccrs)

    assert "1/1 release sync(s) → PR" in body
    assert "ACTIONS TAKEN" in body


def test_sync_line_omitted_when_syncs_attempted_zero():
    """sync line must not appear when syncs_attempted is 0 (switch off or nothing pending)."""
    stats = {
        **_ok_stats(new_signals=2, ccrs_created=1),
        "syncs_attempted": 0,
        "syncs_succeeded": 0,
        "syncs_failed": 0,
        "syncs_skipped": 0,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] topic")]
    _, body = build_digest(run, ccrs)

    assert "release sync" not in body


def test_sync_failure_line_renders_in_actions_needed():
    """When syncs_failed > 0, an ACTIONS NEEDED warning line is emitted."""
    stats = {
        **_ok_stats(new_signals=2, ccrs_created=1),
        "syncs_attempted": 1,
        "syncs_succeeded": 0,
        "syncs_failed": 1,
        "syncs_skipped": 0,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] topic")]
    _, body = build_digest(run, ccrs)

    assert "release sync failing" in body
    assert "SyncLog" in body
    assert "ACTIONS NEEDED" in body


def test_sync_failure_line_omitted_when_no_failures():
    """No ACTIONS NEEDED sync line when syncs_failed == 0."""
    stats = {
        **_ok_stats(new_signals=2, ccrs_created=1),
        "syncs_attempted": 1,
        "syncs_succeeded": 1,
        "syncs_failed": 0,
        "syncs_skipped": 0,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] topic")]
    _, body = build_digest(run, ccrs)

    assert "release sync failing" not in body


# ---------------------------------------------------------------------------
# Task 5: changesets_generated digest line
# ---------------------------------------------------------------------------


def test_normal_shape_with_changesets_renders_generation_line():
    """When changesets_generated is in stats, the generation line is rendered."""
    stats = {
        **_ok_stats(new_signals=3, ccrs_created=2),
        "gaps_judged": 2,
        "gaps_adopted": 2,
        "gaps_monitored": 0,
        "gaps_strengthened": 0,
        "gaps_rejected": 0,
        "gaps_resurrected": 0,
        "changesets_generated": 2,
        "changesets_failed": 0,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] A"), _ccr("[AI] B")]
    _, body = build_digest(run, ccrs)

    assert "2 change-set(s) generated" in body
    assert "0 degraded to proposal-only" in body


def test_normal_shape_with_partial_changesets_renders_degraded_count():
    """changesets_failed is included in the generation line."""
    stats = {
        **_ok_stats(new_signals=3, ccrs_created=3),
        "gaps_judged": 3,
        "gaps_adopted": 3,
        "gaps_monitored": 0,
        "gaps_strengthened": 0,
        "gaps_rejected": 0,
        "gaps_resurrected": 0,
        "changesets_generated": 1,
        "changesets_failed": 2,
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] A"), _ccr("[AI] B"), _ccr("[AI] C")]
    _, body = build_digest(run, ccrs)

    assert "1 change-set(s) generated" in body
    assert "2 degraded to proposal-only" in body


def test_normal_shape_without_changesets_key_omits_generation_line():
    """When changesets_generated is absent from stats, no generation line is emitted.

    This mirrors Phase-2 runs where the kill switch was OFF — the line must be
    absent so the digest is not polluted with zero-value generation noise.
    """
    stats = {
        **_ok_stats(new_signals=3, ccrs_created=2),
        "gaps_judged": 2,
        "gaps_adopted": 2,
        "gaps_monitored": 0,
        "gaps_strengthened": 0,
        "gaps_rejected": 0,
        "gaps_resurrected": 0,
        # No changesets_generated key — kill switch was OFF.
    }
    run = _run("ok", stats)
    ccrs = [_ccr("[AI] A"), _ccr("[AI] B")]
    _, body = build_digest(run, ccrs)

    assert "change-set" not in body
    assert "degraded to proposal-only" not in body
