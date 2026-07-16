"""Tests for the deck QA gate (S3 — port of CareerFoundry's slide QA rubric).

The conforming positive fixture is CareerFoundry's shipped M2.1 exemplar deck
(``fixtures/exemplar_deck.md``). Negatives are produced by mutating ONE element
of that deck so a single mechanical gate flips to ``fail`` — proving the gate
names the specific problem. The genuinely-visual gates (the D-2
human-review-before-release layer) always report ``needs_human``.

Gate provenance (see docs/SLIDE_SYSTEM.md):
  - Mechanical blockers ported/adapted from ``scripts/qa_check.py`` +
    RELEASE_STANDARD_v1.0.0.md §4 (deck structure) and §2 (theme).
  - ``needs_human`` gates ported from RELEASE_STANDARD §7 "QA gates (blocking
    before share)" — the visual/click-through checks a human must clear.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.slides.qa import (
    FAIL,
    NEEDS_HUMAN,
    PASS,
    DeckQAReport,
    deck_ready_to_ship,
    qa_deck,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "exemplar_deck.md"
EXEMPLAR = _FIXTURE.read_text(encoding="utf-8")

# The mechanical gates that contribute to ``report.passed`` (all must be PASS).
MECHANICAL_GATES = {
    "frontmatter_theme",
    "act_structure",
    "slide_count",
    "slide_density",
    "code_blocks_present",
    "code_fence_hygiene",
    "no_placeholder_links",
    "anti_pattern_or_prod_tip",
    "self_assessment_rubric",
    "references_block",
}
HUMAN_GATES = {
    "metadata_leak_scan",
    "visual_layout_clearance",
    "visual_regression_slides",
    "links_clickable_in_export",
    "live_url_spotcheck",
}


def _gate(report: DeckQAReport, name: str):
    match = [g for g in report.gates if g.name == name]
    assert match, f"gate {name!r} not present in report"
    return match[0]


# ---------------------------------------------------------------------------
# Positive: the shipped exemplar clears every mechanical gate.
# ---------------------------------------------------------------------------


def test_exemplar_passes_mechanical_gates():
    report = qa_deck(EXEMPLAR)
    assert report.passed is True, [g for g in report.gates if g.status == FAIL]
    for name in MECHANICAL_GATES:
        assert _gate(report, name).status == PASS, f"{name} not PASS"


def test_report_lists_every_gate_once():
    report = qa_deck(EXEMPLAR)
    names = [g.name for g in report.gates]
    assert set(names) == MECHANICAL_GATES | HUMAN_GATES
    assert len(names) == len(set(names)), "duplicate gate in report"


# ---------------------------------------------------------------------------
# The D-2 human-review layer: visual gates always need a human.
# ---------------------------------------------------------------------------


def test_visual_gates_need_human():
    report = qa_deck(EXEMPLAR)
    for name in (
        "visual_layout_clearance",
        "visual_regression_slides",
        "links_clickable_in_export",
        "live_url_spotcheck",
    ):
        assert _gate(report, name).status == NEEDS_HUMAN


def test_needs_human_gates_do_not_block_passed():
    # Exemplar mentions "widget" (support widget) in the visible body → the
    # metadata leak scan asks a human to confirm it is not a leak, but that must
    # NOT fail the mechanical gate set.
    report = qa_deck(EXEMPLAR)
    leak = _gate(report, "metadata_leak_scan")
    assert leak.status == NEEDS_HUMAN
    assert "widget" in leak.detail
    assert report.passed is True


def test_rendered_artifacts_noted_in_human_gates():
    from app.slides.render import RenderedDeck

    rendered = RenderedDeck(pdf=b"%PDF", pptx=b"pptx", html=b"<html>")
    report = qa_deck(EXEMPLAR, rendered=rendered)
    # With artifacts supplied, the human-review gates point at them.
    assert "artifact" in _gate(report, "visual_regression_slides").detail.lower()


# ---------------------------------------------------------------------------
# Negatives: mutate ONE element → the SPECIFIC gate fails.
# ---------------------------------------------------------------------------


def test_missing_theme_fails_frontmatter_gate():
    broken = EXEMPLAR.replace("theme: career-forge\n", "", 1)
    report = qa_deck(broken)
    gate = _gate(report, "frontmatter_theme")
    assert gate.status == FAIL
    assert "theme" in gate.detail
    assert report.passed is False


def test_missing_act_fails_act_structure_with_named_act():
    broken = EXEMPLAR.replace("<!-- ACT 4:", "<!-- SECTION 4:", 1)
    report = qa_deck(broken)
    gate = _gate(report, "act_structure")
    assert gate.status == FAIL
    assert "4" in gate.detail


def test_too_few_slides_fails_slide_count():
    tiny = (
        "---\nmarp: true\ntheme: career-forge\n---\n\n"
        "## One\n\ntext\n\n---\n\n## Two\n\ntext\n"
    )
    gate = _gate(qa_deck(tiny), "slide_count")
    assert gate.status == FAIL
    assert "2" in gate.detail


def test_code_slide_without_dense_fails_density():
    overcrowded = EXEMPLAR + (
        "\n---\n\n## Bonus Code\n\n```python\nprint('crowded, no dense class')\n```\n"
    )
    gate = _gate(qa_deck(overcrowded), "slide_density")
    assert gate.status == FAIL
    assert "Bonus Code" in gate.detail


def test_no_code_blocks_fails_code_presence():
    # Strip every fenced block → the live-code act has no code.
    stripped = re.sub(r"```.*?```", "", EXEMPLAR, flags=re.S)
    gate = _gate(qa_deck(stripped), "code_blocks_present")
    assert gate.status == FAIL


def test_pandoc_fence_attrs_fail_hygiene():
    broken = EXEMPLAR.replace("```python", "```python {.line-numbers}", 1)
    gate = _gate(qa_deck(broken), "code_fence_hygiene")
    assert gate.status == FAIL
    assert "line-numbers" in gate.detail


def test_learn_placeholder_link_fails_placeholder_gate():
    broken = EXEMPLAR.replace(
        "## The Problem You Will Solve",
        "## The Problem You Will Solve\n\nSee [the primer](/learn/agent-loop).",
        1,
    )
    gate = _gate(qa_deck(broken), "no_placeholder_links")
    assert gate.status == FAIL
    assert "/learn/" in gate.detail


def test_missing_anti_pattern_and_prod_tip_fails():
    stripped = EXEMPLAR
    for token in ("🟥", "🟩", "callout-anti", "callout-prod"):
        stripped = stripped.replace(token, "")
    stripped = re.sub(r"(?i)anti-pattern|production tip", "", stripped)
    gate = _gate(qa_deck(stripped), "anti_pattern_or_prod_tip")
    assert gate.status == FAIL


def test_missing_rubric_fails_self_assessment_gate():
    stripped = re.sub(r"(?i)assessment rubric|self-assessment|self_assessment", "x", EXEMPLAR)
    gate = _gate(qa_deck(stripped), "self_assessment_rubric")
    assert gate.status == FAIL


def test_missing_references_fails_references_gate():
    stripped = EXEMPLAR.replace("REFERENCES:", "notes:")
    gate = _gate(qa_deck(stripped), "references_block")
    assert gate.status == FAIL


# ---------------------------------------------------------------------------
# Release readiness: mechanical green AND human review clears.
# ---------------------------------------------------------------------------


def test_ready_to_ship_requires_both_mechanical_and_human():
    report = qa_deck(EXEMPLAR)
    assert report.passed is True
    # Mechanical green but human review not yet cleared → not shippable.
    assert deck_ready_to_ship(report, human_review_passed=False) is False
    # Both cleared → shippable.
    assert deck_ready_to_ship(report, human_review_passed=True) is True


def test_ready_to_ship_false_when_mechanical_fails():
    broken = EXEMPLAR.replace("theme: career-forge\n", "", 1)
    report = qa_deck(broken)
    assert report.passed is False
    # Even with a human sign-off, failing mechanical gates block the ship.
    assert deck_ready_to_ship(report, human_review_passed=True) is False
