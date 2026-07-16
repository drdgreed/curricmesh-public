"""T3b — the tutor seam on ``AIClient`` threads the session language into the
model prompt.

Mirrors tests/authoring_ai/test_author_ai_client.py: monkeypatch ``_parse`` and
assert the composed user prompt for each of the three tutor seam methods
(answer / coach / assess) carries the "Respond in {language}." instruction when
non-default, and is unchanged for the English default. ZERO real Anthropic calls.
"""

from __future__ import annotations

import pytest

from app.ai.client import AIClient
from app.ai.tutor import AssessmentEvaluation, CoachMessage, TutorAnswer


def _install_capture(client: AIClient, captured: dict, ret):
    async def fake_parse(*, system, user, output_format):
        captured["system"] = system
        captured["user"] = user
        return ret

    client._parse = fake_parse  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_answer_threads_language_into_prompt():
    client = AIClient(api_key="test")
    captured: dict = {}
    _install_capture(client, captured, TutorAnswer(answer="ok"))

    await client.answer_question(
        question="q", context_chunks=["excerpt"], language="French"
    )
    assert "Respond in French." in captured["user"]
    # D5: the language string carries no identity; the system prompt is unchanged.
    assert "learner" not in captured["user"].lower() or "LEARNER QUESTION" in captured["user"]


@pytest.mark.asyncio
async def test_coach_threads_language_into_prompt():
    client = AIClient(api_key="test")
    captured: dict = {}
    _install_capture(client, captured, CoachMessage(message="ok"))

    await client.generate_coaching(
        progress="- Completed: 1 of 3", context_chunks=[], language="French"
    )
    assert "Respond in French." in captured["user"]


@pytest.mark.asyncio
async def test_assess_threads_language_into_prompt():
    client = AIClient(api_key="test")
    captured: dict = {}
    _install_capture(client, captured, AssessmentEvaluation(score=0.5, feedback="ok"))

    await client.evaluate_submission(
        rubric="r", assessment_prompt="p", response="a", language="French"
    )
    assert "Respond in French." in captured["user"]


@pytest.mark.asyncio
async def test_default_en_leaves_tutor_prompts_unchanged():
    client = AIClient(api_key="test")
    captured: dict = {}

    _install_capture(client, captured, TutorAnswer(answer="ok"))
    await client.answer_question(question="q", context_chunks=["e"])
    assert "Respond in" not in captured["user"]

    _install_capture(client, captured, CoachMessage(message="ok"))
    await client.generate_coaching(progress="p", context_chunks=[], language="English")
    assert "Respond in" not in captured["user"]

    _install_capture(client, captured, AssessmentEvaluation(score=0.5, feedback="ok"))
    await client.evaluate_submission(
        rubric="r", assessment_prompt="p", response="a", language="en"
    )
    assert "Respond in" not in captured["user"]
