"""Task 3 — rule-based intake heuristics (pure functions, no DB).

Covers each branch of ``app.builder.categorize``: word counting, the code
detection (positive + negative), slide-break counting, and every keyword arm of
``guess_kind`` plus its ``lesson_plan`` default.
"""

from __future__ import annotations

from app.builder.categorize import extract_metrics, guess_kind
from app.models.enums import AssetKind

# ---------------------------------------------------------------------------
# extract_metrics
# ---------------------------------------------------------------------------


def test_word_count_of_a_sentence():
    m = extract_metrics("the quick brown fox jumps")
    assert m["word_count"] == 5
    assert "lines_of_code" not in m
    assert "slide_count" not in m


def test_six_line_python_block_is_code():
    code = (
        "import os\n"
        "def main():\n"
        "    x = os.getenv('X')\n"
        "    if x:\n"
        "        print(x)\n"
        "    return x\n"
    )
    m = extract_metrics(code)
    assert m["lines_of_code"] == 6


def test_prose_paragraph_has_no_lines_of_code():
    prose = (
        "Retrieval augmented generation grounds a model in your data.\n"
        "It fetches relevant passages first.\n"
        "Then it conditions the answer on them.\n"
    )
    m = extract_metrics(prose)
    assert "lines_of_code" not in m
    assert m["word_count"] > 0


def test_slide_breaks_counted_as_separators_plus_one():
    deck = "Intro\n---\nBody\n---\nOutro"
    m = extract_metrics(deck)
    assert m["slide_count"] == 3  # two `---` separators -> three slides


def test_empty_text_yields_empty_metrics():
    assert extract_metrics(None) == {}
    assert extract_metrics("") == {}


# ---------------------------------------------------------------------------
# guess_kind
# ---------------------------------------------------------------------------


def test_guess_kind_quiz_is_assessment():
    assert guess_kind("Week 3 Quiz") is AssetKind.assessment


def test_guess_kind_slides():
    assert guess_kind("RAG Slides") is AssetKind.slides


def test_guess_kind_project():
    assert guess_kind("Capstone Project") is AssetKind.project


def test_guess_kind_rubric():
    assert guess_kind("Grading Rubric") is AssetKind.rubric


def test_guess_kind_lab_and_exercise():
    assert guess_kind("Hands-on Lab") is AssetKind.lab
    assert guess_kind("Practice Exercise") is AssetKind.lab


def test_guess_kind_deck_alias():
    assert guess_kind("Pitch Deck") is AssetKind.slides


def test_guess_kind_assessment_synonyms():
    assert guess_kind("Final Exam") is AssetKind.assessment
    assert guess_kind("Unit Test") is AssetKind.assessment
    assert guess_kind("Module Assessment") is AssetKind.assessment


def test_guess_kind_objectives():
    assert guess_kind("Learning Objectives") is AssetKind.learning_objectives


def test_guess_kind_spec_starter_references():
    assert guess_kind("API Spec") is AssetKind.spec
    assert guess_kind("Starter Code") is AssetKind.starter
    assert guess_kind("Further References") is AssetKind.references


def test_guess_kind_defaults_to_lesson_plan():
    assert guess_kind("Introduction to Agents") is AssetKind.lesson_plan


def test_guess_kind_falls_through_to_text():
    # Title gives no signal; the text does.
    assert guess_kind("Module 1", "Please complete the lab below.") is AssetKind.lab


# ---------------------------------------------------------------------------
# Word-boundary false-positive regression tests (Task 4 cleanup of Task 3)
# ---------------------------------------------------------------------------


def test_guess_kind_latest_techniques_is_not_assessment():
    """'test' must not match inside 'techniques' — word-boundary required."""
    assert guess_kind("Latest Techniques in ML") is AssetKind.lesson_plan


def test_guess_kind_collaborative_is_not_lab():
    """'lab' must not match inside 'collaborative' — word-boundary required."""
    assert guess_kind("Collaborative Learning") is AssetKind.lesson_plan


def test_guess_kind_real_lab_still_matches():
    """A genuine 'lab' token in the title still resolves to lab."""
    assert guess_kind("Lab 3: Recursion") is AssetKind.lab
