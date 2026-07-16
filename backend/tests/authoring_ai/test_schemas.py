"""Authoring Platform slice 3 — per-aspect generator output schemas.

These are structured-output contracts for the ``CourseAuthorAI`` seam. The
tests pin the field shape (so the router + client can rely on it) and assert
the model-steering field descriptions exist (grounding/safety discipline —
never invent, surface uncertainty in caveats).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedItemContent,
    GeneratedObjective,
    GeneratedObjectives,
)


def test_generated_objectives_round_trip():
    """GeneratedObjectives wraps a list of Bloom-tagged objectives (object, not bare array)."""
    obj = GeneratedObjectives(
        objectives=[
            GeneratedObjective(
                text="Explain how a transformer attention head works",
                bloom_level="understand",
                key_skills=["attention", "transformers"],
            )
        ]
    )
    assert len(obj.objectives) == 1
    assert obj.objectives[0].bloom_level == "understand"
    assert obj.objectives[0].key_skills == ["attention", "transformers"]


def test_generated_objective_key_skills_defaults_empty():
    o = GeneratedObjective(text="Do a thing", bloom_level="apply")
    assert o.key_skills == []


def test_generated_objective_rejects_bad_bloom_level():
    with pytest.raises(ValidationError):
        GeneratedObjective(text="x", bloom_level="memorize")  # not a Bloom verb


def test_generated_item_content_shape():
    c = GeneratedItemContent(
        kind="lab",
        content_markdown="# Lab\nBuild an agent.",
        summary="A hands-on lab building a tool-using agent.",
        caveats=["Verify the SDK version before publishing."],
    )
    assert c.kind == "lab"
    assert c.content_markdown.startswith("# Lab")
    assert c.caveats == ["Verify the SDK version before publishing."]


def test_generated_item_content_caveats_default_empty():
    c = GeneratedItemContent(
        kind="lesson_plan", content_markdown="body", summary="s"
    )
    assert c.caveats == []


def test_generated_assessment_shape():
    a = GeneratedAssessment(
        content_markdown="## Quiz\n1. What is MCP?",
        rubric="Full credit: correct definition + one use case.",
        caveats=[],
    )
    assert a.content_markdown.startswith("## Quiz")
    assert a.rubric.startswith("Full credit")
    assert a.caveats == []


def test_all_generator_schemas_have_steering_descriptions():
    """Every field must carry a model-steering description (grounding discipline)."""
    for model in (GeneratedObjective, GeneratedItemContent, GeneratedAssessment):
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} lacks a description"

    # The caveats field must explicitly steer the model to surface uncertainty
    # rather than invent facts — the freshness-pipeline safety discipline.
    for model in (GeneratedItemContent, GeneratedAssessment):
        desc = model.model_fields["caveats"].description.lower()
        assert "verif" in desc or "invent" in desc or "uncertain" in desc
