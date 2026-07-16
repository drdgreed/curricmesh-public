"""Shared fakes + seeding for the tutor (B3) tests.

``FakeTutorAI`` — records every ``answer_question`` call (so tests can assert
D5: what actually reached the model) and returns a canned answer. Zero network.
It also flags whether it was called at all, so the grounding-gate test can prove
the model is NEVER invoked when retrieval is empty.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tutor import AssessmentEvaluation, CoachMessage, TutorAnswer
from app.core.content_hash import content_hash
from app.core.retrieval.embedder import FakeEmbedder
from app.core.retrieval.ingest import ingest_version
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.enums import AssetKind
from app.models.learner import AssessmentSubmission, Enrollment
from tests.retrieval._helpers import seed_version_with_members


@dataclass
class _Call:
    question: str
    context_chunks: list[str]
    language: str = "en"


@dataclass
class _CoachCall:
    progress: str
    context_chunks: list[str]
    language: str = "en"


@dataclass
class _AssessCall:
    rubric: str
    assessment_prompt: str
    response: str
    language: str = "en"


@dataclass
class FakeTutorAI:
    """A fake ``Tutor`` seam. Captures every call; returns fixed outputs.

    ``calls`` / ``coach_calls`` / ``assess_calls`` record exactly what was handed
    to the model — the acceptance tests assert on them to prove D5 (no identity,
    redacted free text; the progress signal reaches the coach prompt). ``called``
    proves the answer grounding gate (must stay False on empty retrieval).
    """

    answer_text: str = "Grounded answer from the course."
    coach_text: str = "You're making good progress — try the next lesson."
    assess_score: float = 0.75
    assess_feedback: str = "Solid response; tighten the rubric's second criterion."
    calls: list[_Call] = field(default_factory=list)
    coach_calls: list[_CoachCall] = field(default_factory=list)
    assess_calls: list[_AssessCall] = field(default_factory=list)

    @property
    def called(self) -> bool:
        return bool(self.calls)

    async def answer_question(
        self, *, question: str, context_chunks: list[str], language: str = "en"
    ) -> TutorAnswer:
        self.calls.append(
            _Call(
                question=question,
                context_chunks=list(context_chunks),
                language=language,
            )
        )
        return TutorAnswer(answer=self.answer_text)

    async def generate_coaching(
        self, *, progress: str, context_chunks: list[str], language: str = "en"
    ) -> CoachMessage:
        self.coach_calls.append(
            _CoachCall(
                progress=progress,
                context_chunks=list(context_chunks),
                language=language,
            )
        )
        return CoachMessage(message=self.coach_text)

    async def evaluate_submission(
        self, *, rubric: str, assessment_prompt: str, response: str,
        language: str = "en",
    ) -> AssessmentEvaluation:
        self.assess_calls.append(
            _AssessCall(
                rubric=rubric,
                assessment_prompt=assessment_prompt,
                response=response,
                language=language,
            )
        )
        return AssessmentEvaluation(
            score=self.assess_score, feedback=self.assess_feedback
        )


async def seed_enrollment_with_index(
    db: AsyncSession, *, texts: list[str], learner_id: uuid.UUID | None = None
) -> Enrollment:
    """Seed a released version + its ContentChunk index, and enroll a learner."""
    version = await seed_version_with_members(db, texts=texts)
    await ingest_version(db, version.id, FakeEmbedder())
    enrollment = Enrollment(
        learner_id=learner_id or uuid.uuid4(),
        curriculum_version_id=version.id,
    )
    db.add(enrollment)
    await db.flush()
    return enrollment


async def mark_item_complete(
    db: AsyncSession, enrollment: Enrollment, *, order: int
) -> None:
    """Mark the enrolled version's item at ``order`` complete for this enrollment."""
    from app.models.learner import LearnerProgress

    member = (
        await db.execute(
            select(VersionMember).where(
                VersionMember.curriculum_version_id
                == enrollment.curriculum_version_id,
                VersionMember.order == order,
            )
        )
    ).scalars().first()
    db.add(
        LearnerProgress(
            enrollment_id=enrollment.id,
            content_member_id=member.id,
            status="complete",
        )
    )
    await db.flush()


async def seed_assessment_submission(
    db: AsyncSession,
    *,
    enrollment: Enrollment,
    response_text: str,
    rubric: str | None = None,
    prompt: str = "Explain retrieval-augmented generation.",
) -> AssessmentSubmission:
    """Add an ``assessment``-kind item (rubric in metadata_) + a learner submission.

    The rubric lives in ``ContentVersion.metadata_["rubric"]`` — the same place
    the authoring path stores it (``ai_notes["rubric"]``). Returns the flushed
    submission.
    """
    asset = LineageAsset(kind=AssetKind.assessment, lineage_key="wk09/assessment")
    db.add(asset)
    await db.flush()
    metadata = {"rubric": rubric} if rubric is not None else {}
    cv = ContentVersion(
        asset_id=asset.id,
        seq=1,
        content=prompt,
        metadata_=metadata,
        content_hash=content_hash("assessment", prompt, metadata),
    )
    db.add(cv)
    await db.flush()
    member = VersionMember(
        curriculum_version_id=enrollment.curriculum_version_id,
        asset_id=asset.id,
        asset_version_id=cv.id,
        section="Week 9",
        week_index=9,
        order=99,
    )
    db.add(member)
    await db.flush()
    submission = AssessmentSubmission(
        enrollment_id=enrollment.id,
        content_member_id=member.id,
        response_text=response_text,
    )
    db.add(submission)
    await db.flush()
    return submission
