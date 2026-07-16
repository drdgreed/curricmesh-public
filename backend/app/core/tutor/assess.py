"""The ``assess`` tutor seam (Phase B, B5) — assessment feedback + scoring.

Grades a learner's ``AssessmentSubmission`` against the assessment item's
**rubric** and writes the resulting ``score`` + ``feedback`` back onto the
submission. Two load-bearing invariants, mirrored from the ``answer`` seam:

* **D5 anonymization.** The only things handed to the model are course material
  (the rubric + the assessment prompt) and ``redact_pii(response_text)`` — NEVER
  the learner's id, name, or email. The FULL un-redacted response stays on
  ``AssessmentSubmission.response_text`` (the secure server-side record); only
  what LEAVES the backend to the model is redacted.
* **Grounding discipline.** The rubric + prompt come from the enrolled version's
  own immutable content. The rubric is read from the assessment item's
  ``ContentVersion.metadata_["rubric"]`` (where the authoring path stores it —
  ``ai_notes["rubric"]`` in the draft model). A missing rubric degrades
  gracefully: the model grades conservatively against the assessment prompt and
  says so — it never invents rubric criteria.

Transaction: only ``flush``es (like ``answer``) so it composes inside the
endpoint's request transaction — the caller commits.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tutor import AssessmentEvaluation, Tutor
from app.core.tutor.redact import redact_pii
from app.models.content_model import ContentVersion, VersionMember
from app.models.learner import AssessmentSubmission


@dataclass
class AssessResult:
    """The graded submission: a 0.0-1.0 score + coaching feedback."""

    score: float
    feedback: str


async def _load_rubric_and_prompt(
    session: AsyncSession, content_member_id
) -> tuple[str, str]:
    """Return ``(rubric, assessment_prompt)`` for the submission's item.

    The assessment prompt is the item's ``ContentVersion.content``; the rubric is
    ``ContentVersion.metadata_["rubric"]`` when present (the authoring path stores
    it there). Both empty-string when absent — the seam degrades gracefully.
    """
    member = await session.get(VersionMember, content_member_id)
    if member is None:
        return "", ""
    content = await session.get(ContentVersion, member.asset_version_id)
    if content is None:
        return "", ""
    assessment_prompt = content.content or ""
    rubric = ""
    meta = content.metadata_ or {}
    if isinstance(meta, dict):
        raw = meta.get("rubric")
        if isinstance(raw, str):
            rubric = raw
        elif raw is not None:
            rubric = str(raw)
    return rubric, assessment_prompt


async def assess(
    session: AsyncSession,
    *,
    submission: AssessmentSubmission,
    tutor_ai: Tutor,
    language: str = "en",
) -> AssessResult:
    """Grade ``submission`` against its item's rubric; write score + feedback back.

    Enforces D5: the raw response is stored (already on ``submission``); only the
    PII-redacted response reaches the model, alongside course-material rubric +
    prompt. No learner identity is ever passed to the model. ``language`` (T3b,
    session-held) localizes only the feedback text — the score stays numeric.
    """
    rubric, assessment_prompt = await _load_rubric_and_prompt(
        session, submission.content_member_id
    )

    # D5 step: redact the response BEFORE it can reach the model. The FULL raw
    # response remains on submission.response_text (secure server-side record).
    redacted_response = redact_pii(submission.response_text)

    # D5: the ONLY inputs are course material (rubric + prompt) + redacted
    # response. No learner identity is passed.
    evaluation: AssessmentEvaluation = await tutor_ai.evaluate_submission(
        rubric=rubric,
        assessment_prompt=assessment_prompt,
        response=redacted_response,
        language=language,
    )

    # Write the score + feedback back onto the submission (the point of B5).
    submission.score = evaluation.score
    submission.feedback = evaluation.feedback
    await session.flush()

    return AssessResult(score=evaluation.score, feedback=evaluation.feedback)
