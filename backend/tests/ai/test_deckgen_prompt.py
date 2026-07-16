"""S2 — deck-generator prompt + grounding (pure, no model call, no DB).

Proves the CI-testable, non-proprietary properties of the deck seam:

  * the SYSTEM PROMPT enforces the grounding / "never invent" honesty discipline;
  * ``build_deck_user_prompt`` places every objective + every item body into the
    prompt (the GROUNDING invariant), degrades gracefully on empty input, and the
    language suffix is empty for the English default.

NOTE (public mirror): the production system prompt additionally encodes a
proprietary house slide standard (narrative arc, locked theme, callout
vocabulary, few-shot exemplar decks). That standard — and the tests that
asserted its verbatim presence — are withheld from this public repository. The
grounding contract below is the reusable engineering and is shown in full.

Structure of the *rendered* deck is validated later by rendering via S1 — out of
scope for CI. These assertions guard the inputs to the model, not its pixels.
"""

from __future__ import annotations

from app.ai.deckgen import (
    DECK_SYSTEM_PROMPT,
    build_deck_user_prompt,
)


# ---------------------------------------------------------------------------
# System prompt enforces the grounding / honesty discipline
# ---------------------------------------------------------------------------


def test_system_prompt_enforces_grounding_and_honesty():
    p = DECK_SYSTEM_PROMPT.lower()
    # Grounded strictly in the supplied objectives + content.
    assert "ground every slide" in p
    assert "objectives" in p and "content items" in p
    # Never fabricate; surface gaps in caveats instead.
    assert "never invent" in p
    for word in ["citation", "benchmark", "date", "caveats"]:
        assert word in p, word
    # Emits a valid Marp deck + diagram specs.
    assert "marp" in p
    assert "diagram_specs" in p


# ---------------------------------------------------------------------------
# User prompt grounds the course's real objectives + content
# ---------------------------------------------------------------------------


def test_user_prompt_grounds_every_objective_and_item_body():
    objectives = [
        {"text": "Assemble a widget from three parts", "bloom_level": "apply"},
        {"text": "Decide when a widget needs rework", "bloom_level": "evaluate"},
    ]
    items = [
        {"title": "Assembly Walkthrough", "kind": "lesson_plan", "content": "The parts list IS the bill of materials."},
        {"title": "Widget QA Lab", "kind": "lab", "content": "Build a widget for order W-001."},
    ]
    up = build_deck_user_prompt(
        module_title="Assembling Widgets",
        module_number="2.1",
        module_id="w1-1-assembling-widgets",
        objectives=objectives,
        items=items,
        bloom_ceiling="apply",
    )
    # Every objective text + its bloom reaches the prompt.
    assert "Assemble a widget from three parts" in up
    assert "Decide when a widget needs rework" in up
    assert "[Bloom: apply]" in up and "[Bloom: evaluate]" in up
    # Every item title, kind, and BODY reaches the prompt (the substance).
    assert "Assembly Walkthrough" in up and "kind: lesson_plan" in up
    assert "The parts list IS the bill of materials." in up
    assert "Widget QA Lab" in up and "Build a widget for order W-001." in up
    # Module identity + bloom ceiling reach the prompt.
    assert "Assembling Widgets" in up and "2.1" in up and "w1-1-assembling-widgets" in up
    assert "TARGET BLOOM CEILING: apply" in up


def test_user_prompt_degrades_gracefully_on_empty_course():
    up = build_deck_user_prompt(
        module_title="Empty",
        module_number="",
        module_id="empty",
        objectives=[],
        items=[],
    )
    assert "(no objectives supplied)" in up
    assert "(no content items supplied)" in up


def test_user_prompt_item_without_body_is_flagged_not_dropped():
    up = build_deck_user_prompt(
        module_title="M",
        module_number="1",
        module_id="m",
        objectives=[{"text": "Do a thing"}],
        items=[{"title": "Stub Item", "kind": "spec", "content": None}],
    )
    # The item is present but its missing body is flagged (never fabricated).
    assert "Stub Item" in up
    assert "do not fabricate" in up.lower()


def test_user_prompt_english_default_adds_no_language_suffix():
    base = dict(module_title="M", module_number="1", module_id="m",
                objectives=[{"text": "x"}], items=[])
    en = build_deck_user_prompt(**base, language="en")
    default = build_deck_user_prompt(**base)
    assert en == default  # 'en' is byte-identical to the default
    assert "Author all slide prose" not in en


def test_user_prompt_non_default_language_appends_instruction():
    up = build_deck_user_prompt(
        module_title="M", module_number="1", module_id="m",
        objectives=[{"text": "x"}], items=[], language="Spanish",
    )
    assert "Spanish" in up
    assert "front-matter keys, class names, and code identifiers unchanged" in up
