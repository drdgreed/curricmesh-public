"""AI-assisted QA judge (LLM-as-judge, 6 dimensions) — Task C3.

``score_qa`` asks a ``QAJudge`` to pre-score a CCR across the six canonical QA
dimensions (1–5 + per-dimension evidence) and persists a **draft** ``QAReview``
that a human QA Lead accepts / edits / overrides.

THE NON-NEGOTIABLE INVARIANT — the AI never passes the release gate:
  ``can_release`` (engine.py) counts ONLY ``QAReview`` rows with
  ``verdict == 'pass'``. The judge writes its review with the sentinel verdict
  ``verdict = 'ai_draft'`` (NOT 'pass'/'fail'), so it is advisory-only and is
  always filtered out of the gate. We construct the ``QAReview`` directly here
  (NOT via ``record_qa``) precisely so we bypass the pass/fail enforcement and
  do NOT emit a 'qa_passed' history event. This is intentional and safe.

Design:
  - ``QAJudge`` is the seam the rest of the app depends on. The real
    ``AIClient`` implements it; tests inject a fake. ZERO real API calls in CI.
  - Engine convention: this module flushes but NEVER commits. The router owns
    the transaction boundary.
  - A malformed judgement (a missing / extra / duplicate dimension) RAISES at
    ``QAJudgement`` validation time — never a silently-partial review.
"""

from __future__ import annotations

import json
import uuid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import ensure_ai_researcher
from app.core.workflow.rules import QA_DIMENSIONS
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.workflow import ChangeRequest, QAReview

# Sentinel verdict for AI-drafted reviews. NOT 'pass'/'fail' — so the release
# gate (which counts only verdict == 'pass') can never be satisfied by the AI.
AI_DRAFT_VERDICT = "ai_draft"


# ---------------------------------------------------------------------------
# Structured-output contract
# ---------------------------------------------------------------------------


class DimensionJudgement(BaseModel):
    """One dimension's score (1–5) plus the evidence that justifies it."""

    dimension: str
    score: int = Field(ge=1, le=5)
    evidence: str


class QAJudgement(BaseModel):
    """Wrapper object for the six per-dimension judgements.

    The validator guarantees the judgements cover EXACTLY the six canonical
    ``QA_DIMENSIONS`` — no missing, no extras, no duplicates — so downstream
    code can build a complete flat score map without re-checking.
    """

    judgements: list[DimensionJudgement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cover_all_dimensions(self) -> "QAJudgement":
        seen = [j.dimension for j in self.judgements]
        seen_set = set(seen)
        expected = set(QA_DIMENSIONS)
        if len(seen) != len(seen_set):
            dupes = sorted({d for d in seen if seen.count(d) > 1})
            raise ValueError(f"Duplicate QA dimensions in judgement: {dupes}")
        if seen_set != expected:
            missing = sorted(expected - seen_set)
            extra = sorted(seen_set - expected)
            raise ValueError(
                f"QA judgement must cover exactly the six dimensions. "
                f"Missing: {missing}. Unexpected: {extra}."
            )
        return self


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


@runtime_checkable
class QAJudge(Protocol):
    """Anything that can turn (ccr_summary, proposed_changes) into a judgement."""

    async def judge(
        self, ccr_summary: str, proposed_changes: str
    ) -> QAJudgement: ...


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _build_summary(ccr: ChangeRequest) -> str:
    return (
        f"Title: {ccr.title}\n"
        f"Proposed bump: {ccr.proposed_bump or '(unspecified)'}\n"
        f"Rationale: {ccr.rationale or '(none)'}"
    )


# The judge must see the ACTUAL generated bodies, not just the impact summary —
# otherwise it scores content it cannot read. But a single CCR can carry an
# entire generated course, so we bound what we feed it: each body is clipped and
# the total is capped. Truncation is flagged inline so the model knows it saw a
# prefix, not the whole body. Generous enough that ordinary edits pass through
# whole; a safety valve only for pathological change_sets.
_PER_ASSET_CHARS = 6_000
_TOTAL_BODY_CHARS = 60_000


def _clip(text: str, limit: int) -> str:
    """Clip ``text`` to ``limit`` chars, flagging how much was dropped."""
    if limit <= 0:
        return f"…[{len(text)} chars omitted for length]"
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


# Mirrors ``app.builder.compile.INITIAL_RELEASE_KEY``. Kept local (not imported)
# so the QA judge doesn't pull in the whole builder/compile import chain.
_INITIAL_RELEASE_KEY = "initial_release"

# One rendered body entry: (verb, kind, lineage_key, body).
_BodyEntry = tuple[str, str | None, str, str]


def _render_bodies(entries: list[_BodyEntry]) -> str:
    """Render ``(verb, kind, lineage_key, body)`` entries into the judge's
    PROPOSED CONTENT section — bounded per-asset and in total, with truncation
    flagged inline. Shared by BOTH content sources: the ``change_set`` path
    (freshness pipeline) and the candidate-version path (authored initial
    release). Entries with an empty body are skipped.
    """
    sections: list[str] = []
    used = 0
    for verb, kind, key, body in entries:
        if not body:
            continue
        if used >= _TOTAL_BODY_CHARS:
            sections.append("…[remaining asset bodies omitted for length]")
            break
        header = f"### {verb} {kind}: {key}" if kind else f"### {verb} asset: {key}"
        clipped = _clip(str(body), min(_PER_ASSET_CHARS, _TOTAL_BODY_CHARS - used))
        sections.append(f"{header}\n{clipped}")
        used += len(clipped)
    return _wrap_bodies(sections)


def _format_change_set_bodies(change_set: dict | None) -> str:
    """Render the actual asset bodies in a ``change_set`` for the QA judge.

    Reads the JSONB dict DEFENSIVELY (never validates against the schema) so a
    partial, legacy, or malformed change_set can never crash QA scoring — a bad
    shape just yields fewer/zero bodies. Returns ``""`` when there is nothing
    with a body to show, so the caller omits the section entirely.
    """
    if not isinstance(change_set, dict):
        return ""
    entries: list[_BodyEntry] = []
    # New assets first (they carry a kind), then edits.
    for verb, items in (("NEW", change_set.get("added")), ("EDITED", change_set.get("changed"))):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            body = item.get("content")
            if not body:  # None or "" — nothing to review for this asset
                continue
            entries.append((verb, item.get("kind"), item.get("lineage_key", "(unknown)"), str(body)))
    return _render_bodies(entries)


def _wrap_bodies(sections: list[str]) -> str:
    if not sections:
        return ""
    return (
        "PROPOSED CONTENT (the actual asset bodies under review):\n\n"
        + "\n\n".join(sections)
    )


def _build_proposed_changes(ccr: ChangeRequest) -> str:
    """Build the 'proposed changes' text the judge scores (synchronous part).

    Combines the impact summary (what changes + why) with the ACTUAL generated
    asset bodies from ``change_set`` (the freshness-pipeline path), so the six
    dimensions — especially content_accuracy and student_experience — are judged
    from the content, not from metadata alone. Authored initial-release bodies
    live in the DB and are appended by ``score_qa`` (see
    ``_load_initial_release_bodies``).
    """
    parts: list[str] = []
    if ccr.impact:
        parts.append("IMPACT ANALYSIS:\n" + json.dumps(ccr.impact, default=str, indent=2))
    bodies = _format_change_set_bodies(ccr.change_set)
    if bodies:
        parts.append(bodies)
    if parts:
        return "\n\n".join(parts)
    return ccr.rationale or "(no structured impact; see rationale)"


async def _load_initial_release_bodies(
    session: AsyncSession, ccr: ChangeRequest
) -> str:
    """Load an AUTHORED initial-release CCR's generated bodies for the judge.

    The Course Builder publish path writes content into immutable
    ``ContentVersion`` rows under a candidate version and leaves
    ``ccr.change_set`` NULL — so ``_build_proposed_changes`` can't see it. Here we
    follow the ``impact['initial_release']['candidate_version_id']`` pointer and
    load every member's body (``VersionMember`` → ``ContentVersion``), rendered
    through the same bounded formatter.

    Returns ``""`` for any non-initial-release CCR, a malformed pointer, or a
    candidate with no content — so the caller appends nothing. Read-only; the
    query is RLS-scoped by the caller's session ``app.current_org`` GUC.
    """
    marker = (ccr.impact or {}).get(_INITIAL_RELEASE_KEY)
    if not isinstance(marker, dict):
        return ""
    raw_id = marker.get("candidate_version_id")
    if not raw_id:
        return ""
    try:
        candidate_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError):
        return ""
    rows = (
        await session.execute(
            select(
                LineageAsset.kind,
                LineageAsset.lineage_key,
                ContentVersion.content,
            )
            .select_from(VersionMember)
            .join(ContentVersion, ContentVersion.id == VersionMember.asset_version_id)
            .join(LineageAsset, LineageAsset.id == VersionMember.asset_id)
            .where(VersionMember.curriculum_version_id == candidate_id)
            .order_by(VersionMember.week_index, VersionMember.order)
        )
    ).all()
    entries: list[_BodyEntry] = [
        ("NEW", getattr(kind, "value", str(kind)), key, content)
        for kind, key, content in rows
    ]
    return _render_bodies(entries)


async def score_qa(
    session: AsyncSession, *, ccr: ChangeRequest, judge: QAJudge
) -> QAReview:
    """Pre-score a CCR's six QA dimensions and persist a DRAFT QAReview.

    The returned review carries ``verdict='ai_draft'`` and so can never satisfy
    the release gate; a human QA Lead must still record a real pass/fail.

    Does NOT commit (the router owns the transaction). Let judge (API) errors
    and validation errors propagate — never write a silently-partial review.
    """
    summary = _build_summary(ccr)
    proposed_changes = _build_proposed_changes(ccr)

    # Authored initial-release CCRs store their generated content in
    # ContentVersion rows (change_set is NULL), which _build_proposed_changes
    # can't see — load those bodies from the candidate version and append. A
    # no-op (empty string) for change_set-based or description-only CCRs.
    initial_release_bodies = await _load_initial_release_bodies(session, ccr)
    if initial_release_bodies:
        proposed_changes = f"{proposed_changes}\n\n{initial_release_bodies}"

    # The QAJudgement validator guarantees all six dimensions are present.
    judgement = await judge.judge(summary, proposed_changes)

    dimension_scores = {j.dimension: j.score for j in judgement.judgements}
    evidence = {j.dimension: j.evidence for j in judgement.judgements}

    # One system actor across AI features (DRY — same helper C2 uses).
    ai_user = await ensure_ai_researcher(session)

    # Constructed directly (NOT via record_qa) so the sentinel verdict bypasses
    # pass/fail enforcement and no 'qa_passed' history event is emitted.
    review = QAReview(
        ccr_id=ccr.id,
        reviewer_id=ai_user.id,
        dimension_scores=dimension_scores,
        evidence=evidence,
        verdict=AI_DRAFT_VERDICT,
    )
    session.add(review)
    await session.flush()
    return review
