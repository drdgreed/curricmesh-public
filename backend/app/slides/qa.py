"""Deck QA gate — port of CareerFoundry's slide QA rubric (S3).

A deck is an artifact that must clear QA before it ships. This module ports the
mechanical half of CareerFoundry's slide QA — ``slide_system_standard/scripts/
qa_check.py`` plus the ``RELEASE_STANDARD_v1.0.0.md`` deck standard — into a
single stateless function, :func:`qa_deck`. It runs the checks that can be made
mechanically and flags the genuinely-visual ones as ``needs_human`` (the D-2
human-review-before-release layer of §7).

Provenance of each gate (faithful to CareerFoundry — the gate list IS the
quality standard):

Mechanical blockers (contribute to :attr:`DeckQAReport.passed`)
  - ``frontmatter_theme``       ADAPTED from Release Standard §2 (theme block) —
                                the deck must declare ``marp: true`` + a
                                ``theme:`` so the render pipeline (S1) can style
                                it. qa_check.py assumed frontmatter; here it is
                                an explicit gate.
  - ``act_structure``           ADAPTED from Release Standard §4 (the 7-act
                                narrative arc). qa_check.py did not assert acts;
                                the standard mandates them.
  - ``slide_count``             PORTED from qa_check.py §1 (slide-block count) +
                                Release Standard §4 ("22–28 slides").
  - ``slide_density``           PORTED from qa_check.py §1 / CONTENT_QA §1 — a
                                code-bearing slide must carry a layout class
                                (``dense``/``wide-diagram``) or it overflows.
  - ``code_blocks_present``     ADAPTED from Release Standard §4 Act 4 ("Live
                                Code Demo — real code").
  - ``code_fence_hygiene``      PORTED verbatim from qa_check.py §3
                                (``bad_fences`` — no pandoc ``{...}`` attrs).
  - ``no_placeholder_links``    ADAPTED from qa_check.py §4 (``bad_paths``),
                                NARROWED to ``/learn/`` placeholders in the
                                visible body. qa_check's broad ``../`` regex
                                false-positives on the legitimate
                                ``../diagrams/*.png`` refs this pipeline uses.
  - ``anti_pattern_or_prod_tip`` ADAPTED from Release Standard §2 (callouts) —
                                a deck carries at least one anti-pattern or
                                production-tip callout.
  - ``self_assessment_rubric``  ADAPTED from Release Standard §4 ("every deck
                                ends with a self-assessment rubric slide").
  - ``references_block``        ADAPTED from the deck convention (speaker-note
                                ``REFERENCES:`` blocks) + qa_check.py §4 links.

Human-review gates (``needs_human`` — never block ``passed``; a human clears
these before ship, per Release Standard §7)
  - ``metadata_leak_scan``      PORTED from qa_check.py §5 (``leak_terms``) — a
                                WARNING in qa_check, i.e. a judgment call, so it
                                surfaces as ``needs_human`` when terms appear.
  - ``visual_layout_clearance``   Release Standard §7.3 (footer/header clearance).
  - ``visual_regression_slides``  Release Standard §7.2 (inspect the code-block
                                  slide + worked-example diagram) + CONTENT_QA §2
                                  (diagram legibility/landscape).
  - ``links_clickable_in_export`` Release Standard §7.4 (click every URL in the
                                  exported PDF).
  - ``live_url_spotcheck``        Release Standard §7.5 (spot-check the live URL
                                  after deploy).

QA is stateless analysis — nothing is persisted (no migration). The release-
readiness rule (:func:`deck_ready_to_ship`) mirrors the course QA→release gate
conceptually: mechanical gates green AND the human review cleared. It does NOT
rebuild the workflow/QA engine; it is the deck-specific check that a later
release hook calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing the render toolchain at QA time
    from app.slides.render import RenderedDeck

# Gate statuses.
PASS = "pass"
FAIL = "fail"
NEEDS_HUMAN = "needs_human"

# Release Standard §4: total target 22–28 slides; the 7-act narrative arc.
SLIDE_MIN = 22
SLIDE_MAX = 28
REQUIRED_ACTS = tuple(range(1, 8))  # ACT 1 … ACT 7

# qa_check.py §3: fenced blocks must not carry pandoc attribute braces.
_BAD_FENCE_RE = re.compile(r"```[a-z]+\s*\{[^}]+\}")
# qa_check.py §5: internal-metadata leak terms (a warning → human judgment).
_LEAK_TERMS = ("widget", "MCQ count", "schema field", "internal id", "in development")
# Layout classes that make a code/diagram-heavy slide safe (CONTENT_QA §1).
_LAYOUT_CLASS_RE = re.compile(r"_class:\s*[^\n]*\b(dense|wide-diagram|title)\b")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A real, language-tagged code fence (``` python``, ``` json``, …). A bare fence
# or an illustrative ``` text`` flow-diagram fence is NOT "code + prose" for the
# density rule — CONTENT_QA §1 targets real code walks that overflow.
_CODE_FENCE_RE = re.compile(r"^```(\w+)", re.MULTILINE)
_NON_CODE_FENCE_LANGS = {"text", "txt"}


@dataclass(frozen=True)
class QAGate:
    """One QA gate outcome: ``status`` is ``pass`` | ``fail`` | ``needs_human``."""

    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DeckQAReport:
    """The full deck QA outcome.

    ``passed`` is the *mechanical* verdict: True iff no gate reports ``fail``
    (only mechanical gates can fail; the human-review gates report ``pass`` or
    ``needs_human``). A deck is ready to ship only when ``passed`` AND the
    human-review gates clear — see :func:`deck_ready_to_ship`.
    """

    passed: bool
    gates: list[QAGate]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _split(deck_md: str) -> tuple[str, list[str]]:
    """Return ``(frontmatter, [slide_block, ...])``.

    Marp separates frontmatter and every slide with a ``---`` line. The first
    split element is the pre-frontmatter empty string, the second is the
    frontmatter, and the rest are slide blocks.
    """
    parts = re.split(r"^---\s*$", deck_md, flags=re.MULTILINE)
    if len(parts) < 3:
        return "", [p for p in parts if p.strip()]
    frontmatter = parts[1]
    slides = [p for p in parts[2:] if p.strip()]
    return frontmatter, slides


def _visible(deck_md: str) -> str:
    """Strip HTML comments (speaker ``_notes``, ``_class`` directives, act
    markers) → the learner-facing slide text. Used by the checks whose intent
    is scoped to visible body (CONTENT_QA §4/§5)."""
    return _HTML_COMMENT_RE.sub("", deck_md)


def _slide_title(block: str) -> str:
    """Best-effort heading for a slide block, for actionable failure details."""
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return "(untitled slide)"


# ---------------------------------------------------------------------------
# Mechanical gates
# ---------------------------------------------------------------------------


def _gate_frontmatter_theme(frontmatter: str) -> QAGate:
    missing = []
    if not re.search(r"^\s*marp:\s*true\s*$", frontmatter, flags=re.MULTILINE):
        missing.append("marp: true")
    if not re.search(r"^\s*theme:\s*\S+", frontmatter, flags=re.MULTILINE):
        missing.append("theme:")
    if missing:
        return QAGate(
            "frontmatter_theme", FAIL,
            f"frontmatter missing required key(s): {', '.join(missing)}",
        )
    return QAGate("frontmatter_theme", PASS, "marp + theme frontmatter present")


def _gate_act_structure(deck_md: str) -> QAGate:
    found = {int(n) for n in re.findall(r"ACT\s+(\d+)\s*:", deck_md)}
    missing = [n for n in REQUIRED_ACTS if n not in found]
    if missing:
        return QAGate(
            "act_structure", FAIL,
            f"missing act marker(s): {', '.join(f'ACT {n}' for n in missing)} "
            f"(Release Standard §4 requires the 7-act arc)",
        )
    return QAGate("act_structure", PASS, "all 7 narrative acts present")


def _gate_slide_count(slides: list[str]) -> QAGate:
    n = len(slides)
    if not (SLIDE_MIN <= n <= SLIDE_MAX):
        return QAGate(
            "slide_count", FAIL,
            f"{n} slides — outside the {SLIDE_MIN}–{SLIDE_MAX} range (§4)",
        )
    return QAGate("slide_count", PASS, f"{n} slides (within {SLIDE_MIN}–{SLIDE_MAX})")


def _has_real_code(block: str) -> bool:
    langs = _CODE_FENCE_RE.findall(block)
    return any(lang.lower() not in _NON_CODE_FENCE_LANGS for lang in langs)


def _gate_slide_density(slides: list[str]) -> QAGate:
    offenders = [
        _slide_title(b)
        for b in slides
        if _has_real_code(b) and not _LAYOUT_CLASS_RE.search(b)
    ]
    if offenders:
        return QAGate(
            "slide_density", FAIL,
            "code-bearing slide(s) missing a dense/wide-diagram layout class "
            f"(overflow risk): {', '.join(offenders)}",
        )
    return QAGate("slide_density", PASS, "code-heavy slides carry a layout class")


def _gate_code_blocks_present(deck_md: str) -> QAGate:
    if deck_md.count("```") < 2:
        return QAGate(
            "code_blocks_present", FAIL,
            "no fenced code block found (§4 Act 4 requires a live-code demo)",
        )
    return QAGate("code_blocks_present", PASS, "fenced code block(s) present")


def _gate_code_fence_hygiene(deck_md: str) -> QAGate:
    bad = _BAD_FENCE_RE.findall(deck_md)
    if bad:
        return QAGate(
            "code_fence_hygiene", FAIL,
            f"pandoc-style fence attributes (Marp ignores them): {bad[:3]}",
        )
    return QAGate("code_fence_hygiene", PASS, "plain language fences only")


def _gate_no_placeholder_links(deck_md: str) -> QAGate:
    body = _visible(deck_md)
    placeholders = re.findall(r"\]\((/learn/[^)]*)\)", body)
    if placeholders:
        return QAGate(
            "no_placeholder_links", FAIL,
            f"placeholder /learn/ link(s) in slide body: {placeholders[:3]}",
        )
    return QAGate("no_placeholder_links", PASS, "no placeholder links in slide body")


def _gate_anti_pattern_or_prod_tip(deck_md: str) -> QAGate:
    has = (
        "🟥" in deck_md
        or "🟩" in deck_md
        or "callout-anti" in deck_md
        or "callout-prod" in deck_md
        or re.search(r"(?i)anti-pattern|production tip", deck_md)
    )
    if not has:
        return QAGate(
            "anti_pattern_or_prod_tip", FAIL,
            "no anti-pattern or production-tip callout found (§2 callouts)",
        )
    return QAGate("anti_pattern_or_prod_tip", PASS, "anti-pattern / production-tip callout present")


def _gate_self_assessment_rubric(deck_md: str) -> QAGate:
    if not re.search(r"(?i)assessment rubric|self-assessment", deck_md):
        return QAGate(
            "self_assessment_rubric", FAIL,
            "no self-assessment rubric slide (§4 requires one before advance)",
        )
    return QAGate("self_assessment_rubric", PASS, "self-assessment rubric slide present")


def _gate_references_block(deck_md: str) -> QAGate:
    if "REFERENCES:" not in deck_md:
        return QAGate(
            "references_block", FAIL,
            "no REFERENCES: block found (speaker-note citation convention)",
        )
    return QAGate("references_block", PASS, "REFERENCES: citation blocks present")


# ---------------------------------------------------------------------------
# Human-review gates (needs_human — never block ``passed``)
# ---------------------------------------------------------------------------


def _gate_metadata_leak_scan(deck_md: str) -> QAGate:
    body = _visible(deck_md)
    found = [t for t in _LEAK_TERMS if re.search(rf"(?i)\b{re.escape(t)}\b", body)]
    if found:
        return QAGate(
            "metadata_leak_scan", NEEDS_HUMAN,
            f"possible internal-metadata term(s) in slide body — confirm not a "
            f"leak: {', '.join(found)}",
        )
    return QAGate("metadata_leak_scan", PASS, "no internal-metadata terms in slide body")


def _human_gate(name: str, what: str, has_render: bool) -> QAGate:
    where = (
        "inspect the rendered artifacts"
        if has_render
        else "render the deck first, then inspect"
    )
    return QAGate(name, NEEDS_HUMAN, f"{what} — {where}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def qa_deck(deck_md: str, *, rendered: "RenderedDeck | None" = None) -> DeckQAReport:
    """Run the deck QA gate over a Marp ``deck.md``.

    - ``deck_md``  — the Marp markdown source.
    - ``rendered`` — the optional S1 :class:`RenderedDeck`; when supplied, the
      human-review gate details point at the artifacts to inspect. The visual
      gates are ``needs_human`` either way (a human must eyeball them).

    Returns a :class:`DeckQAReport`. ``passed`` is the mechanical verdict; the
    ``needs_human`` gates are the D-2 human-review layer and never affect it.
    """
    frontmatter, slides = _split(deck_md)
    has_render = rendered is not None

    gates = [
        # Mechanical blockers.
        _gate_frontmatter_theme(frontmatter),
        _gate_act_structure(deck_md),
        _gate_slide_count(slides),
        _gate_slide_density(slides),
        _gate_code_blocks_present(deck_md),
        _gate_code_fence_hygiene(deck_md),
        _gate_no_placeholder_links(deck_md),
        _gate_anti_pattern_or_prod_tip(deck_md),
        _gate_self_assessment_rubric(deck_md),
        _gate_references_block(deck_md),
        # Human-review layer (Release Standard §7).
        _gate_metadata_leak_scan(deck_md),
        _human_gate(
            "visual_layout_clearance",
            "confirm no content overruns the footer/header on any slide (§7.3)",
            has_render,
        ),
        _human_gate(
            "visual_regression_slides",
            "visually inspect the code-block slide and the worked-example "
            "diagram — the regression-test artifacts (§7.2, §2 legibility)",
            has_render,
        ),
        _human_gate(
            "links_clickable_in_export",
            "click every URL in the exported PDF to confirm it resolves (§7.4)",
            has_render,
        ),
        _human_gate(
            "live_url_spotcheck",
            "spot-check the deck on its live platform URL after deploy (§7.5)",
            has_render,
        ),
    ]

    passed = all(g.status != FAIL for g in gates)
    return DeckQAReport(passed=passed, gates=gates)


def deck_ready_to_ship(report: DeckQAReport, *, human_review_passed: bool) -> bool:
    """Release-readiness rule: a deck ships only when the mechanical gates are
    green (``report.passed``) AND the human review has cleared the
    ``needs_human`` gates.

    This mirrors the course QA→release model conceptually (mechanical check +
    human sign-off) without touching the workflow/QA engine — QA is stateless
    analysis. A release hook wires this in by refusing to publish a deck until
    it returns True.
    """
    return report.passed and human_review_passed
