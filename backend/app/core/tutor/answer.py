"""The ``answer`` tutor seam (Phase B, B3) — RAG-grounded Q&A.

Orchestrates one tutor turn end-to-end, enforcing the two load-bearing
invariants:

* **Grounding gate (the load-bearing acceptance).** The tutor answers ONLY from
  the enrolled version's own retrieved ``ContentChunk``s. When retrieval returns
  nothing, it returns a grounded *refusal* and NEVER calls the model to
  fabricate — the ``tutor_ai`` seam is not invoked at all in that path. When it
  does answer, it cites the source chunks (their ids + ``source_member_id``).
* **D5 anonymization.** The only things handed to the model are the retrieved
  course excerpts and ``redact_pii(question)`` — never the learner's id, name,
  email, or any identity field. Identity separation is the primary control;
  outbound PII redaction is defense-in-depth. The FULL un-redacted question +
  answer are persisted server-side (``TutorConversation`` / ``TutorMessage``).

Transaction: this only ``flush``es (like ``ingest_version``) so it composes
inside the endpoint's request transaction — the caller commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tutor import Tutor
from app.core.retrieval.embedder import Embedder
from app.core.retrieval.retrieve import retrieve
from app.core.tutor.redact import redact_pii
from app.models.learner import Enrollment
from app.models.tutor import TutorConversation, TutorMessage

# The grounded refusal returned when retrieval yields no course context. MUST
# match the within-context refusal the tutor prompt instructs the model to use.
REFUSAL_TEXT = "I don't have information about that in this course."

# How many chunks to ground on. Small: keeps the prompt bounded + cheap.
DEFAULT_K = 5

# How much of each chunk to keep as a citation snippet (chars).
_SNIPPET_CHARS = 240


@dataclass
class Citation:
    """A pointer back to the course material that grounded an answer."""

    chunk_id: uuid.UUID
    source_member_id: uuid.UUID | None
    snippet: str

    def as_json(self) -> dict:
        return {
            "chunk_id": str(self.chunk_id),
            "source_member_id": (
                str(self.source_member_id)
                if self.source_member_id is not None
                else None
            ),
            "snippet": self.snippet,
        }


@dataclass
class AnswerResult:
    """The tutor's turn: the answer text, its citations, and the thread id."""

    text: str
    conversation_id: uuid.UUID
    citations: list[Citation] = field(default_factory=list)


async def answer(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    question: str,
    embedder: Embedder,
    tutor_ai: Tutor,
    conversation: TutorConversation | None = None,
    k: int = DEFAULT_K,
    language: str = "en",
) -> AnswerResult:
    """Answer ``question`` grounded in ``enrollment``'s pinned version.

    Creates the conversation if not supplied. Persists the FULL un-redacted
    learner turn and the tutor turn. Enforces the grounding gate and D5.
    ``language`` (T3b, session-held) steers only the reply language — it carries
    no identity, so D5 is unaffected, and it does NOT relax the grounding gate
    (an empty retrieval still returns the refusal without calling the model).
    """
    # D5 step 1: redact the question BEFORE it can reach the model. The raw
    # question is still recorded server-side below.
    redacted_question = redact_pii(question)

    # Grounding: retrieve the enrolled version's own chunks. Tenant + version
    # scoped inside retrieve(). The model NEVER sees the learner identity — we
    # pass only the redacted question here.
    chunks = await retrieve(
        session,
        version_id=enrollment.curriculum_version_id,
        query=redacted_question,
        k=k,
        embedder=embedder,
    )

    # Conversation thread (opaque per-enrollment handle — not a user id).
    if conversation is None:
        conversation = TutorConversation(enrollment_id=enrollment.id)
        session.add(conversation)
        await session.flush()

    # Server-side record: the FULL un-redacted learner question (D5 step 3).
    session.add(
        TutorMessage(
            conversation_id=conversation.id,
            role="learner",
            text=question,
            citations=None,
        )
    )

    # ---- Grounding gate ---------------------------------------------------
    # No course context → grounded refusal. The model is NOT called: we never
    # ask it to fabricate an answer from nothing.
    if not chunks:
        session.add(
            TutorMessage(
                conversation_id=conversation.id,
                role="tutor",
                text=REFUSAL_TEXT,
                citations=[],
            )
        )
        await session.flush()
        return AnswerResult(
            text=REFUSAL_TEXT, conversation_id=conversation.id, citations=[]
        )

    # ---- Grounded answer --------------------------------------------------
    # D5: the ONLY inputs to the model are the redacted question + chunk texts.
    generated = await tutor_ai.answer_question(
        question=redacted_question,
        context_chunks=[c.text for c in chunks],
        language=language,
    )

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
            text=generated.answer,
            citations=[c.as_json() for c in citations],
        )
    )
    await session.flush()
    return AnswerResult(
        text=generated.answer,
        conversation_id=conversation.id,
        citations=citations,
    )
