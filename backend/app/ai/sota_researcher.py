"""SOTA-gap researcher orchestration (C2).

``analyze_gaps`` prompts a ``GapExtractor`` with the version's covered topics
plus the industry corpus, then drafts a real ``ChangeRequest`` per genuine gap
via ``submit_ccr`` — the AI never bypasses the normal QA/approval flow. Each
draft is authored by the system "AI Researcher" actor (no elevated rights).

Engine convention: this module flushes but NEVER commits. The router owns the
transaction boundary.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import GapExtractor
from app.ai.schemas import CorpusDoc, GapFinding
from app.core.actors import ensure_ai_researcher
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import submit_ccr
from app.core.workflow.rules import WorkflowError
from app.models.sota import SotaFinding, SotaSource
from app.models.structure import Module, Project
from app.models.version import Version
from app.models.workflow import ChangeRequest

logger = logging.getLogger(__name__)


async def _covered_topics(session: AsyncSession, version: Version) -> list[str]:
    """Derive the curriculum's topical surface from Module.focus + Project.title.

    LO ``body_ref`` is a gs:// pointer (not inline text), so module focuses and
    project titles are the dereferenceable topical surface for a version.
    """
    focus_result = await session.execute(
        select(Module.focus)
        .where(Module.version_id == version.id, Module.focus.isnot(None))
        .order_by(Module.index)
    )
    topics = [f for (f,) in focus_result.all() if f]

    title_result = await session.execute(
        select(Project.title)
        .where(Project.version_id == version.id)
        .order_by(Project.index)
    )
    topics.extend(t for (t,) in title_result.all() if t)
    return topics


async def create_gap_ccr(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    finding: GapFinding,
    author_id: uuid.UUID,
) -> ChangeRequest | None:
    """Create one gap CCR + its ai_research impact + SotaFinding row.
    Returns None if the workflow guard rejects the bump (mirrors analyze_gaps'
    log-and-skip behavior). Flushes; never commits."""
    bump = BumpType(finding.proposed_bump)
    try:
        ccr = await submit_ccr(
            session,
            curriculum_id=curriculum_id,
            author_id=author_id,
            title=f"[AI] {finding.topic}",
            rationale=finding.rationale,
            proposed_bump=bump,
            affected_kinds=set(),
            instructor_override=False,
        )
    except WorkflowError as exc:
        # Cohort guard rejects minor/major mid-cohort. Log, skip, continue.
        logger.warning(
            "Skipping AI gap draft for topic %r (bump=%s): %s",
            finding.topic,
            finding.proposed_bump,
            exc,
        )
        return None

    ccr.impact = {
        "ai_research": {
            "topic": finding.topic,
            "coverage_status": finding.coverage_status,
            "citations": finding.evidence,
        }
    }
    session.add(ccr)

    session.add(
        SotaFinding(
            curriculum_id=curriculum_id,
            topic=finding.topic,
            evidence={
                "citations": finding.evidence,
                "ccr_id": str(ccr.id),
                "proposed_bump": finding.proposed_bump,
            },
            coverage_status=finding.coverage_status,
        )
    )
    await session.flush()
    return ccr


async def analyze_gaps(
    session: AsyncSession,
    *,
    curriculum_id: uuid.UUID,
    version: Version,
    corpus: list[SotaSource],
    extractor: GapExtractor,
) -> list[ChangeRequest]:
    """Analyze SOTA gaps and draft a CCR per genuine gap.

    Each finding is submitted through ``submit_ccr`` (never an INSERT), so the
    draft enters the normal workflow with its audit record. A finding whose
    bump is rejected by the mid-cohort guard (``WorkflowError``) is logged and
    skipped — the batch is not aborted.

    Returns the list of created ``ChangeRequest`` rows. Does NOT commit.
    """
    covered_topics = await _covered_topics(session, version)
    corpus_docs = [
        CorpusDoc(title=s.title, kind=s.kind, body=s.body or "") for s in corpus
    ]

    # Let extractor (API) errors propagate — never swallow into [].
    findings = await extractor.extract_gaps(covered_topics, corpus_docs)

    ai_user = await ensure_ai_researcher(session)

    created: list[ChangeRequest] = []
    for finding in findings:
        ccr = await create_gap_ccr(
            session,
            curriculum_id=curriculum_id,
            finding=finding,
            author_id=ai_user.id,
        )
        if ccr is not None:
            created.append(ccr)

    return created
