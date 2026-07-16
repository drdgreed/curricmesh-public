"""The ``coach`` tutor seam (Phase B, B4) — proactive next-step coaching.

Assembles a coaching turn from the learner's **Phase-2 progress**
(``LearnerProgress``) plus retrieved course chunks for their *next* objective,
then persists it to the tutor conversation store. Two load-bearing invariants,
mirrored from the ``answer`` seam:

* **D5 anonymization.** The only things handed to the model are an *abstracted*
  progress signal (completed/total counts + the next item's section + lineage
  label) and the retrieved course excerpts — NEVER the learner's id, name, or
  email. Identity separation is the primary control; the progress signal is
  built here from counts and content labels only, so no identity can leak into
  the prompt.
* **Grounding discipline.** Course specifics are grounded in the enrolled
  version's own ``ContentChunk``s (retrieved for the next objective). Unlike
  ``answer``, an empty index does NOT hard-refuse — coaching still runs on the
  real progress facts — but the model is instructed not to fabricate course
  material when no excerpts are supplied (graceful degradation).

Transaction: only ``flush``es (like ``answer`` / ``ingest_version``) so it
composes inside the endpoint's request transaction — the caller commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tutor import CoachMessage, Tutor
from app.core.retrieval.embedder import Embedder
from app.core.retrieval.retrieve import retrieve
from app.core.tutor.answer import Citation, _SNIPPET_CHARS
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.learner import Enrollment, LearnerProgress
from app.models.tutor import TutorConversation, TutorMessage

# How many chunks to ground the coaching on. Small: bounded + cheap.
DEFAULT_K = 5


@dataclass
class CoachResult:
    """The coaching turn: the message, its citations, and the thread id."""

    text: str
    conversation_id: uuid.UUID
    citations: list[Citation] = field(default_factory=list)


async def _load_ordered_items(
    session: AsyncSession, version_id: uuid.UUID
) -> list[tuple[VersionMember, ContentVersion, LineageAsset]]:
    """The version's items in learner order (week, then order)."""
    stmt = (
        select(VersionMember, ContentVersion, LineageAsset)
        .join(ContentVersion, ContentVersion.id == VersionMember.asset_version_id)
        .join(LineageAsset, LineageAsset.id == VersionMember.asset_id)
        .where(VersionMember.curriculum_version_id == version_id)
        .order_by(VersionMember.week_index, VersionMember.order)
    )
    return list((await session.execute(stmt)).all())


async def _completed_member_ids(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> set[uuid.UUID]:
    """The set of member ids this enrollment has marked complete."""
    stmt = select(LearnerProgress.content_member_id).where(
        LearnerProgress.enrollment_id == enrollment_id,
        LearnerProgress.status == "complete",
    )
    return set((await session.execute(stmt)).scalars().all())


def _build_progress_signal(
    total: int,
    completed: int,
    next_member: VersionMember | None,
    next_asset: LineageAsset | None,
) -> str:
    """An ANONYMIZED progress signal for the model — counts + labels, NO identity.

    Contains only aggregate counts and the next item's section + lineage label.
    Deliberately carries no learner id/name/email — the primary D5 control.
    """
    lines = [f"- Completed: {completed} of {total} items."]
    if next_member is None:
        lines.append("- Next up: all items complete — the learner has finished.")
    else:
        label = next_asset.lineage_key if next_asset is not None else "next item"
        lines.append(
            f'- Next up: section "{next_member.section}", item "{label}".'
        )
    return "\n".join(lines)


async def coach(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    embedder: Embedder,
    tutor_ai: Tutor,
    conversation: TutorConversation | None = None,
    k: int = DEFAULT_K,
    language: str = "en",
) -> CoachResult:
    """Produce a proactive coaching turn for ``enrollment``.

    Uses the learner's ``LearnerProgress`` to find where they are + their next
    objective, grounds course specifics in that objective's retrieved chunks,
    and persists the coaching message. Enforces D5 (no identity to the model).
    ``language`` (T3b, session-held) steers only the reply language — no identity.
    """
    items = await _load_ordered_items(session, enrollment.curriculum_version_id)
    completed = await _completed_member_ids(session, enrollment.id)

    total = len(items)
    done = sum(1 for member, _, _ in items if member.id in completed)

    # The next objective = the first item not yet complete (learner order).
    next_member: VersionMember | None = None
    next_content: ContentVersion | None = None
    next_asset: LineageAsset | None = None
    for member, content, asset in items:
        if member.id not in completed:
            next_member, next_content, next_asset = member, content, asset
            break

    # Ground course specifics in the next objective's own chunks. If there is no
    # next objective (all complete), there is nothing to retrieve.
    chunks = []
    if next_content is not None and next_content.content:
        chunks = await retrieve(
            session,
            version_id=enrollment.curriculum_version_id,
            query=next_content.content,
            k=k,
            embedder=embedder,
        )

    progress = _build_progress_signal(total, done, next_member, next_asset)

    # D5: the ONLY inputs to the model are the anonymized progress signal + the
    # retrieved course excerpts. No learner identity is passed.
    generated: CoachMessage = await tutor_ai.generate_coaching(
        progress=progress,
        context_chunks=[c.text for c in chunks],
        language=language,
    )

    # Conversation thread (opaque per-enrollment handle — not a user id).
    if conversation is None:
        conversation = TutorConversation(enrollment_id=enrollment.id)
        session.add(conversation)
        await session.flush()

    citations = [
        Citation(
            chunk_id=c.id,
            source_member_id=c.source_member_id,
            snippet=c.text[:_SNIPPET_CHARS],
        )
        for c in chunks
    ]
    session.add(
        TutorMessage(
            conversation_id=conversation.id,
            role="tutor",
            text=generated.message,
            citations=[c.as_json() for c in citations],
        )
    )
    await session.flush()
    return CoachResult(
        text=generated.message,
        conversation_id=conversation.id,
        citations=citations,
    )
