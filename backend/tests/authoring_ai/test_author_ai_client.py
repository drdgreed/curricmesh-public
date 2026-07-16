"""Authoring Platform slice 3 — CourseAuthorAI seam on AIClient.

Mirrors tests/freshness_pipeline/test_judge_client.py: monkeypatch ``_parse``
and assert (1) the correct structured-output ``output_format`` is requested,
(2) the caller's grounding inputs reach the user prompt, and (3) the governed
(grounded + conservative) system prompt is the one actually sent. ZERO real
Anthropic calls.
"""

from __future__ import annotations

import pytest

from app.ai.client import AIClient, CourseAuthorAI
from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedItemContent,
    GeneratedObjective,
    GeneratedObjectives,
)


def _canned_objectives() -> GeneratedObjectives:
    return GeneratedObjectives(
        objectives=[
            GeneratedObjective(
                text="Explain the MCP tool-use protocol",
                bloom_level="understand",
                key_skills=["mcp", "tool use"],
            )
        ]
    )


def _canned_item_content() -> GeneratedItemContent:
    return GeneratedItemContent(
        kind="lab",
        content_markdown="# Lab\nBuild an MCP server.",
        summary="Hands-on lab building an MCP server.",
        caveats=["Verify the SDK version."],
    )


def _canned_assessment() -> GeneratedAssessment:
    return GeneratedAssessment(
        content_markdown="## Quiz\n1. Define MCP.",
        rubric="Full credit: correct definition + use case.",
        caveats=[],
    )


def test_aiclient_satisfies_course_author_ai_protocol():
    assert isinstance(AIClient(api_key="test"), CourseAuthorAI)


# ---------------------------------------------------------------------------
# generate_objectives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_objectives_uses_correct_output_format_and_grounding(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured.update(system=system, user=user, output_format=output_format)
        return _canned_objectives()

    monkeypatch.setattr(client, "_parse", fake_parse)
    result = await client.generate_objectives(
        topic="Model Context Protocol",
        learner_profile={"experience_level": "senior", "role": "backend engineer"},
        count=4,
    )

    assert captured["output_format"] is GeneratedObjectives
    # Topic + learner profile + requested count must reach the model.
    assert "Model Context Protocol" in captured["user"]
    assert "senior" in captured["user"]
    assert "4" in captured["user"]
    # The governed objectives system prompt must be the one sent.
    assert "objective" in captured["system"].lower()
    assert result.objectives[0].bloom_level == "understand"


# ---------------------------------------------------------------------------
# generate_item_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_item_content_uses_correct_output_format_and_grounding(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured.update(system=system, user=user, output_format=output_format)
        return _canned_item_content()

    monkeypatch.setattr(client, "_parse", fake_parse)
    await client.generate_item_content(
        objective="Build a tool-using agent",
        kind="lab",
        course_context="COURSE: AI Engineering 101",
    )

    assert captured["output_format"] is GeneratedItemContent
    assert "Build a tool-using agent" in captured["user"]
    assert "lab" in captured["user"]
    assert "AI Engineering 101" in captured["user"]
    assert "content" in captured["system"].lower()


# ---------------------------------------------------------------------------
# generate_assessment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_assessment_uses_correct_output_format_and_grounding(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured.update(system=system, user=user, output_format=output_format)
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)
    await client.generate_assessment(
        objective="Diagnose a failing agent loop",
        course_context="COURSE: AI Engineering 101",
    )

    assert captured["output_format"] is GeneratedAssessment
    assert "Diagnose a failing agent loop" in captured["user"]
    assert "AI Engineering 101" in captured["user"]
    assert "assessment" in captured["system"].lower()


# ---------------------------------------------------------------------------
# T3a — generation language threads into every generator prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_language_reaches_every_generator_prompt(monkeypatch):
    """A non-default ``language`` appends the target-language instruction to each
    per-aspect generator's user prompt."""
    client = AIClient(api_key="test")
    captured: dict = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        # Return the shape the caller expects for whichever generator ran.
        if output_format is GeneratedObjectives:
            return _canned_objectives()
        if output_format is GeneratedItemContent:
            return _canned_item_content()
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)

    await client.generate_objectives(
        topic="Agents", learner_profile={}, count=3, language="Spanish"
    )
    assert "Generate all content in Spanish." in captured["user"]

    await client.generate_item_content(
        objective="Build an agent", kind="lab", course_context="c", language="Spanish"
    )
    assert "Generate all content in Spanish." in captured["user"]

    await client.generate_assessment(
        objective="Grade an agent", course_context="c", language="Spanish"
    )
    assert "Generate all content in Spanish." in captured["user"]


@pytest.mark.asyncio
async def test_generation_language_default_en_leaves_prompt_unchanged(monkeypatch):
    """The English default (and its synonyms) add NO language instruction — the
    default-brief prompts are byte-identical to pre-T3a behaviour."""
    client = AIClient(api_key="test")
    captured: dict = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_objectives()

    monkeypatch.setattr(client, "_parse", fake_parse)

    for lang in ("en", "English", "  EN  ", ""):
        await client.generate_objectives(
            topic="Agents", learner_profile={}, count=3, language=lang
        )
        assert "Generate all content in" not in captured["user"]

    # And the implicit default (no language kwarg) is likewise unchanged.
    await client.generate_objectives(topic="Agents", learner_profile={}, count=3)
    assert "Generate all content in" not in captured["user"]


@pytest.mark.asyncio
async def test_generate_methods_return_canned_objects(monkeypatch):
    """Each method returns exactly what _parse produced (thin wrapper)."""
    client = AIClient(api_key="test")

    async def fake_objectives(*, system, user, output_format):
        return _canned_objectives()

    async def fake_item(*, system, user, output_format):
        return _canned_item_content()

    async def fake_assessment(*, system, user, output_format):
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_objectives)
    objs = await client.generate_objectives(topic="x", learner_profile={})
    assert objs.objectives[0].text == "Explain the MCP tool-use protocol"

    monkeypatch.setattr(client, "_parse", fake_item)
    item = await client.generate_item_content(
        objective="x", kind="lab", course_context="c"
    )
    assert item.kind == "lab"

    monkeypatch.setattr(client, "_parse", fake_assessment)
    assess = await client.generate_assessment(objective="x", course_context="c")
    assert assess.rubric.startswith("Full credit")
