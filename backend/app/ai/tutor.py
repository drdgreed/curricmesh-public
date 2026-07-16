"""Tutor AI seam — the governed ``answer`` generator (Phase B, B3).

The RAG Q&A tutor's model call, isolated behind a Protocol so the real
``AIClient`` implements it (through its ``_parse`` telemetry path) and tests
inject a fake — ZERO real Anthropic calls in CI. Mirrors ``QAJudge`` /
``CourseAuthorAI``.

Two D5 invariants are baked into the seam's SHAPE, not just its prompt:

* **Identity separation.** ``answer_question`` accepts ONLY the (already
  PII-redacted) question text and the retrieved course excerpts. There is no
  parameter through which a learner id/name/email could reach the model — the
  type system enforces the primary D5 control.
* **Grounding.** The system prompt forbids outside knowledge: the model answers
  strictly from the supplied excerpts. The orchestrator
  (``app/core/tutor/answer.py``) additionally NEVER calls this seam when
  retrieval is empty — so the model is never asked to answer from no context.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

# T3b — session-language tutor. The learner picks a language at session start; it
# rides each tutor request as ``language`` and steers ONLY the model's reply
# language. The English default tokens map to NO suffix (prompts stay
# byte-identical to pre-T3b); any other value appends a "Respond in {language}."
# instruction. This carries NO learner identity, so D5 is unaffected.
_DEFAULT_LANGUAGE_TOKENS = {"", "en", "english"}


def _respond_in(language: str) -> str:
    """Prompt suffix instructing the tutor to reply in ``language``.

    Empty for the English default (``en`` / ``english`` / blank) so the default
    tutor prompts are unchanged; otherwise a single trailing instruction
    ``"\\n\\nRespond in {language}."``. Pure — unit-testable without a model call.
    """
    if language.strip().lower() in _DEFAULT_LANGUAGE_TOKENS:
        return ""
    return f"\n\nRespond in {language.strip()}."


class TutorAnswer(BaseModel):
    """The model's grounded answer. Citations are attached by the orchestrator
    from the retrieved chunks, not chosen by the model."""

    answer: str


# The conservative, grounded system prompt. No learner identity is ever
# referenced; the model is told to answer only from the provided excerpts and to
# say so plainly when they don't cover the question (a within-context refusal).
TUTOR_SYSTEM_PROMPT = (
    "You are a course tutor. Answer the learner's question using ONLY the "
    "COURSE EXCERPTS provided below. Do not use any outside knowledge, and do "
    "not speculate. If the excerpts do not contain enough information to answer, "
    "reply exactly: \"I don't have information about that in this course.\" "
    "Be concise, accurate, and grounded strictly in the excerpts. You do not "
    "know and must not ask who the learner is."
)


def build_tutor_user_prompt(
    question: str, context_chunks: list[str], language: str = "en"
) -> str:
    """Compose the user prompt from the course excerpts + the (redacted) question.

    The ONLY inputs are course content and the learner's redacted question — no
    identity fields. Kept a pure function so it can be unit-tested for the D5
    invariant independently of any model call. ``language`` (T3b) appends a
    reply-language instruction when non-default; it carries no identity (D5 holds).
    """
    excerpts = "\n\n".join(
        f"[Excerpt {i + 1}]\n{text}" for i, text in enumerate(context_chunks)
    ) or "(no excerpts)"
    return (
        "COURSE EXCERPTS:\n"
        f"{excerpts}\n\n"
        "LEARNER QUESTION:\n"
        f"{question}\n\n"
        "Answer using only the excerpts above."
        f"{_respond_in(language)}"
    )


# ===========================================================================
# B4 — proactive coaching seam
# ===========================================================================


class CoachMessage(BaseModel):
    """The tutor's proactive coaching turn — a short check-in + ONE next step."""

    message: str


# The coaching system prompt. Forge-Coach-style: warm, course-scoped, checks
# understanding, names exactly ONE concrete next step. Two invariants baked in:
# it must NOT invent course specifics beyond the supplied excerpts, and it never
# references who the learner is (identity never reaches this seam — D5).
COACH_SYSTEM_PROMPT = (
    "You are a supportive course coach. Using the learner's ANONYMIZED PROGRESS "
    "and the COURSE EXCERPTS for their next objective, write a short, encouraging "
    "coaching message that (1) checks understanding of where they are and (2) "
    "names exactly ONE concrete next step. Ground any course specifics STRICTLY "
    "in the excerpts — do not invent lesson content. If no excerpts are provided, "
    "coach on process and the next step generally, without fabricating course "
    "material. You do not know and must not ask who the learner is; never request "
    "personal information. Be concise (2-4 sentences)."
)


def build_coach_user_prompt(
    progress: str, context_chunks: list[str], language: str = "en"
) -> str:
    """Compose the coach prompt from anonymized progress + next-objective excerpts.

    The ONLY inputs are the abstracted progress signal (counts + section/next-item
    label — never identity) and retrieved course excerpts. Pure function so the
    D5 invariant is unit-testable independent of any model call. ``language`` (T3b)
    appends a reply-language instruction when non-default; it carries no identity.
    """
    excerpts = "\n\n".join(
        f"[Excerpt {i + 1}]\n{text}" for i, text in enumerate(context_chunks)
    ) or "(no excerpts)"
    return (
        "ANONYMIZED PROGRESS:\n"
        f"{progress}\n\n"
        "COURSE EXCERPTS (next objective):\n"
        f"{excerpts}\n\n"
        "Write the coaching message now."
        f"{_respond_in(language)}"
    )


# ===========================================================================
# B5 — assessment feedback seam
# ===========================================================================


class AssessmentEvaluation(BaseModel):
    """The model's grading of a submission: a 0.0-1.0 score + coaching feedback."""

    score: float
    feedback: str


# The assessment system prompt. Grades a learner's response against the item's
# rubric, conservatively. Same D5 posture: no learner identity ever reaches this
# seam (the response text is PII-redacted upstream; the rubric + prompt are course
# material). Scores on a 0.0-1.0 scale so the store's Float column is well-defined.
ASSESS_SYSTEM_PROMPT = (
    "You are a fair assessment grader. Score the learner's RESPONSE against the "
    "RUBRIC for the assessment, on a scale of 0.0 to 1.0 (1.0 = fully meets the "
    "rubric). Then write specific, encouraging coaching feedback that references "
    "the rubric criteria and names concrete improvements. Judge ONLY against the "
    "rubric and the assessment prompt provided — do not invent criteria. If the "
    "rubric is empty, grade conservatively against the assessment prompt and say "
    "so in the feedback. You do not know and must not ask who the learner is; "
    "never request or infer personal information."
)


def build_assess_user_prompt(
    rubric: str, assessment_prompt: str, response: str, language: str = "en"
) -> str:
    """Compose the assess prompt from the rubric + assessment prompt + (redacted)
    response.

    The ONLY inputs are course material (rubric + assessment prompt) and the
    learner's PII-redacted response — never identity. Pure function so D5 is
    unit-testable independent of any model call. ``language`` (T3b) appends a
    feedback-language instruction when non-default; it carries no identity. Only
    the coaching feedback is localized — the numeric score is language-agnostic.
    """
    return (
        "ASSESSMENT PROMPT:\n"
        f"{assessment_prompt or '(none provided)'}\n\n"
        "RUBRIC:\n"
        f"{rubric or '(no rubric provided)'}\n\n"
        "LEARNER RESPONSE:\n"
        f"{response}\n\n"
        "Score (0.0-1.0) and give coaching feedback grounded in the rubric."
        f"{_respond_in(language)}"
    )


@runtime_checkable
class Tutor(Protocol):
    """The seam: three governed tutor calls (Q&A / coaching / assessment).

    NOTE every signature: no learner-identity parameter exists on any method.
    The real ``AIClient`` implements all three via its governed ``_parse`` path;
    tests inject a fake so CI makes no real Anthropic call.
    """

    async def answer_question(
        self, *, question: str, context_chunks: list[str], language: str = "en"
    ) -> TutorAnswer: ...

    async def generate_coaching(
        self, *, progress: str, context_chunks: list[str], language: str = "en"
    ) -> CoachMessage: ...

    async def evaluate_submission(
        self, *, rubric: str, assessment_prompt: str, response: str,
        language: str = "en",
    ) -> AssessmentEvaluation: ...
