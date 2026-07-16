"""Judge routing service for the freshness pipeline (Phase 2).

``route_finding`` is the single entry point for the state machine: given a
detected gap finding it looks up the ``GapAssessment`` row for
``(curriculum_id, topic_lower)``, applies the 4-case state machine, makes
exactly the right number of judge calls, and returns a ``JudgeOutcome``
describing what happened.

Contract:
- Flushes; NEVER commits (router/runner owns the transaction boundary).
- On judge-call errors: the exception propagates — the runner's failure
  semantics (fail the run, don't advance seen-state) apply automatically.
- Org scoping is automatic: the session's ``app.current_org`` GUC is already
  set by the caller (org_scoped_session), so RLS filters lookups to the right
  tenant without extra kwargs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import GapJudge
from app.ai.schemas import GapFinding, NetBenefitAssessment
from app.ai.sota_researcher import create_gap_ccr
from app.config import settings
from app.models.freshness_pipeline import GapAssessment
from app.models.workflow import ChangeRequest


@dataclass
class JudgeOutcome:
    action: str                  # "adopted" | "monitored" | "rejected" | "skipped"
    assessment: GapAssessment
    ccr: ChangeRequest | None
    resurrected: bool = False    # True when a reject row was re-judged this call
    strengthened: bool = False   # True when re-eval confidence rose vs prev_confidence


def _build_scores(assessment: NetBenefitAssessment, old_confidence: float | None = None) -> dict:
    """Produce the JSONB ``scores`` dict from a NetBenefitAssessment.

    Always stores the full model_dump() (7 dims + recommendation + confidence +
    rationale) PLUS an explicit ``model_recommendation`` key so callers can read
    the model's raw verdict without unpacking ``recommendation`` (which may be
    the gated value, not the model's original).  On re-evaluations,
    ``prev_confidence`` is appended for the delta display in the Monitor Queue.
    """
    scores: dict = assessment.model_dump()
    scores["model_recommendation"] = assessment.recommendation
    if old_confidence is not None:
        scores["prev_confidence"] = old_confidence
    return scores


def _action_from_recommendation(recommendation: str) -> str:
    return {
        "adopt_now": "adopted",
        "monitor": "monitored",
        "reject": "rejected",
    }.get(recommendation, "monitored")


async def _apply_gate(
    session: AsyncSession,
    row: GapAssessment,
    assessment: NetBenefitAssessment,
    finding: GapFinding,
    curriculum_id: uuid.UUID,
    author_id: uuid.UUID,
) -> ChangeRequest | None:
    """Apply the confidence gate to the judge's verdict and update ``row``.

    Sets ``row.recommendation`` to the GATED value (may differ from the model's
    raw recommendation when adopt_now confidence falls below the threshold).
    Sets ``row.last_evaluated_at``.  On a gated adopt: creates a CCR via
    ``create_gap_ccr`` and enriches it with the full assessment + dossier.

    Returns the created CCR, or None.  Does NOT flush — caller is responsible.
    """
    threshold = settings.FRESHNESS_ADOPT_MIN_CONFIDENCE
    row.last_evaluated_at = datetime.now(timezone.utc)

    if assessment.recommendation == "adopt_now" and assessment.confidence >= threshold:
        ccr = await create_gap_ccr(
            session,
            curriculum_id=curriculum_id,
            finding=finding,
            author_id=author_id,
        )
        if ccr is not None:
            # Copy-then-set (enrich pattern): preserve existing impact keys.
            impact = dict(ccr.impact or {})
            impact["assessment"] = assessment.model_dump()
            impact["dossier"] = row.dossier
            ccr.impact = impact
            session.add(ccr)
            row.promoted_ccr_id = ccr.id
            row.recommendation = "adopt_now"
            return ccr
        else:
            # Workflow guard rejected (e.g. mid-cohort bump) — timing problem,
            # not a value judgment. Treat as monitor so it re-judges next run.
            row.recommendation = "monitor"
            return None

    elif assessment.recommendation == "adopt_now":
        # Below-threshold adopt_now: demote to monitor.
        # The model's original recommendation is preserved in scores["model_recommendation"].
        row.recommendation = "monitor"
        return None

    elif assessment.recommendation == "monitor":
        row.recommendation = "monitor"
        return None

    else:  # reject
        row.recommendation = "reject"
        return None


async def route_finding(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    finding: GapFinding,
    judge: GapJudge,
    covered_topics: list[str],
    run_date: str,
    author_id: uuid.UUID,
) -> JudgeOutcome:
    """Route one gap finding through the 4-case state machine.

    Cases (evaluated in order):
    1. No row           → judge once → store row → apply gate.
    2. reject row       → times_seen++; if doubled → resurrection re-judge; else skip.
    3. monitor row      → times_seen++, append sighting, re-judge, apply gate.
    4. promoted row     → times_seen++, skip (already has a CCR).

    Identity key: ``topic_lower = finding.topic.lower()``.  The session's RLS GUC
    scopes the lookup to the caller's org automatically.

    Flushes; never commits.
    """
    topic_lower = finding.topic.lower()
    sighting = {"run_date": run_date, "source_kinds": ["corpus"], "evidence": list(finding.evidence)}

    result = await session.execute(
        select(GapAssessment).where(
            GapAssessment.curriculum_id == curriculum_id,
            GapAssessment.topic == topic_lower,
        )
    )
    existing = result.scalar_one_or_none()

    # ── Case 4: already promoted (promoted_ccr_id set) ──────────────────────
    if existing is not None and existing.promoted_ccr_id is not None:
        existing.times_seen += 1
        session.add(existing)
        await session.flush()
        return JudgeOutcome(action="skipped", assessment=existing, ccr=None)

    # ── Case 2: reject ───────────────────────────────────────────────────────
    if existing is not None and existing.recommendation == "reject":
        existing.times_seen += 1
        if existing.times_seen >= 2 * existing.times_seen_at_last_eval:
            # Resurrection: append sighting, re-judge, update bookkeeping.
            old_confidence = existing.confidence
            dossier = list(existing.dossier) + [sighting]
            existing.dossier = dossier
            assessment = await judge.judge_gap(finding, covered_topics, dossier)
            existing.times_seen_at_last_eval = existing.times_seen
            existing.confidence = assessment.confidence
            existing.rationale = assessment.rationale
            existing.scores = _build_scores(assessment, old_confidence=old_confidence)
            session.add(existing)
            ccr = await _apply_gate(session, existing, assessment, finding, curriculum_id, author_id)
            await session.flush()
            return JudgeOutcome(
                action=_action_from_recommendation(existing.recommendation),
                assessment=existing,
                ccr=ccr,
                resurrected=True,
                strengthened=assessment.confidence > old_confidence,
            )
        else:
            session.add(existing)
            await session.flush()
            return JudgeOutcome(action="skipped", assessment=existing, ccr=None)

    # ── Case 3: monitor — or an ORPHANED adopt_now (its CCR was deleted, so
    # promoted_ccr_id was SET NULL by the FK). Both mean "known topic, no live
    # proposal": append the sighting and re-judge with the accumulated dossier.
    # Without the orphan arm, such a row would fall through to Case 1 and
    # attempt a duplicate INSERT (unique-violation; found by T5 review tests).
    if existing is not None and (
        existing.recommendation == "monitor"
        or (existing.recommendation == "adopt_now" and existing.promoted_ccr_id is None)
    ):
        old_confidence = existing.confidence
        existing.times_seen += 1
        dossier = list(existing.dossier) + [sighting]
        existing.dossier = dossier
        assessment = await judge.judge_gap(finding, covered_topics, dossier)
        existing.confidence = assessment.confidence
        existing.rationale = assessment.rationale
        existing.scores = _build_scores(assessment, old_confidence=old_confidence)
        strengthened = assessment.confidence > old_confidence
        session.add(existing)
        ccr = await _apply_gate(session, existing, assessment, finding, curriculum_id, author_id)
        await session.flush()
        return JudgeOutcome(
            action=_action_from_recommendation(existing.recommendation),
            assessment=existing,
            ccr=ccr,
            strengthened=strengthened,
        )

    # ── Case 1: no row (first sighting) ─────────────────────────────────────
    dossier = [sighting]
    assessment = await judge.judge_gap(finding, covered_topics, dossier)
    scores = _build_scores(assessment)

    row = GapAssessment(
        curriculum_id=curriculum_id,
        topic=topic_lower,
        display_topic=finding.topic,
        recommendation="monitor",   # placeholder; _apply_gate writes the gated value
        confidence=assessment.confidence,
        scores=scores,
        rationale=assessment.rationale,
        dossier=dossier,
        times_seen=1,
        times_seen_at_last_eval=1,
    )
    session.add(row)
    await session.flush()   # assign PK before _apply_gate may set promoted_ccr_id

    ccr = await _apply_gate(session, row, assessment, finding, curriculum_id, author_id)
    await session.flush()
    return JudgeOutcome(
        action=_action_from_recommendation(row.recommendation),
        assessment=row,
        ccr=ccr,
    )
