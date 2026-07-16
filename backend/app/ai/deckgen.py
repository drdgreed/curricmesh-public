"""Deck-generator AI seam — the governed ``generate_deck`` author call (S2).

Turns a course's objectives + content items into a Marp ``deck.md`` that the S1
pipeline renders to PDF/PPTX/HTML. The real ``AIClient`` implements the
``generate_deck`` method through its governed ``_parse`` telemetry path, and
tests inject a fake — ZERO real Anthropic calls in CI. The output
``GeneratedDeck`` is ADVISORY (D-2): fully AI-generated but a human reviews it
before release.

Conservative + grounded: the deck is grounded STRICTLY in the supplied
objectives and item content; the model must never invent facts, citations,
benchmarks, dates, or version numbers — gaps go in ``caveats``, not the slides.

NOTE (public mirror): the production ``DECK_SYSTEM_PROMPT`` encodes a
proprietary house slide standard (a fixed narrative arc, a locked visual theme,
a pedagogical callout vocabulary, and few-shot exemplar decks). That standard is
withheld from this public repository. The placeholder below keeps the seam
honest and functional — the grounding contract (``build_deck_user_prompt``), the
Protocol seam, and the ``GeneratedDeck`` schema are the reusable engineering and
are shown in full.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ai.schemas import GeneratedDeck


# ---------------------------------------------------------------------------
# The deck-generator SYSTEM PROMPT.
#
# Public-mirror placeholder. The production prompt encodes a proprietary slide
# standard (narrative-arc structure, a locked theme, a callout vocabulary, and
# few-shot exemplar decks) and is intentionally omitted here. This generic
# prompt keeps the seam runnable and preserves the non-proprietary grounding /
# honesty discipline.
# ---------------------------------------------------------------------------

DECK_SYSTEM_PROMPT = (
    "You are a curriculum slide author. Turn a module's learning objectives and "
    "content items into ONE complete, valid Marp `deck.md` that a deterministic "
    "pipeline renders to PDF, PPTX, and HTML.\n\n"
    "OUTPUT: a single `deck_markdown` string (a valid Marp deck), a "
    "`diagram_specs` list mirroring any structural diagrams, a `summary`, and "
    "`caveats`.\n\n"
    "GROUNDING & HONESTY (non-negotiable):\n"
    "- Ground EVERY slide strictly in the supplied learning objectives and "
    "content items. The objectives set the spine; the item bodies supply the "
    "substance.\n"
    "- NEVER invent facts, citations, benchmarks, statistics, dates, or version "
    "numbers the source content did not provide. When you would need a fact you "
    "do not have, omit it or surface it in `caveats` — never assert it.\n"
    "- If the course content is thin, produce the best coherent skeleton you can "
    "and record every gap in `caveats`. Never pad with fabricated content.\n"
)


@runtime_checkable
class DeckGenerator(Protocol):
    """The S2 seam: turns a course's objectives + content into a Marp ``deck.md``.

    The real ``AIClient`` implements this via its governed ``_parse`` path; tests
    inject a fake so CI makes ZERO real Anthropic calls. Mirrors ``Tutor`` /
    ``CourseAuthorAI``. The result is ADVISORY (D-2): reviewed by a human before
    release, never auto-published.
    """

    async def generate_deck(
        self,
        *,
        module_title: str,
        module_number: str,
        module_id: str,
        objectives: list[dict],
        items: list[dict],
        bloom_ceiling: str | None = None,
        language: str = "en",
    ) -> GeneratedDeck: ...


# The default English language tokens map to NO suffix so a default course
# produces a byte-identical prompt; any other value appends an explicit
# target-language instruction (mirrors the tutor/generator language handling).
_DEFAULT_LANGUAGE_TOKENS = {"", "en", "english"}


def _generate_in(language: str) -> str:
    """Prompt suffix instructing the author to emit the deck in ``language``.

    Empty for the English default; otherwise a single trailing instruction.
    Pure — unit-testable without a model call.
    """
    if language.strip().lower() in _DEFAULT_LANGUAGE_TOKENS:
        return ""
    return (
        f"\n\nAuthor all slide prose, callouts, and speaker notes in "
        f"{language.strip()} (keep the front-matter keys, class names, and code "
        f"identifiers unchanged)."
    )


def build_deck_user_prompt(
    *,
    module_title: str,
    module_number: str,
    module_id: str,
    objectives: list[dict],
    items: list[dict],
    bloom_ceiling: str | None = None,
    language: str = "en",
) -> str:
    """Compose the deck-author user prompt from the course's real content.

    Pure function (no I/O) so the GROUNDING invariant is unit-testable without a
    model call: every objective's text and every item's title + content body is
    placed into the prompt so the model authors slides grounded in them.

    ``objectives`` items are dicts with at least ``text`` (and optional
    ``bloom_level``); ``items`` are dicts with ``title``, ``kind``, and optional
    ``content``. Missing/empty content degrades gracefully to a labeled
    placeholder rather than dropping the item.
    """
    obj_lines = []
    for i, obj in enumerate(objectives, 1):
        text = (obj.get("text") or "").strip() or "(untitled objective)"
        bloom = obj.get("bloom_level")
        suffix = f"  [Bloom: {bloom}]" if bloom else ""
        obj_lines.append(f"{i}. {text}{suffix}")
    objectives_block = "\n".join(obj_lines) or "(no objectives supplied)"

    item_blocks = []
    for i, item in enumerate(items, 1):
        title = (item.get("title") or "").strip() or "(untitled item)"
        kind = item.get("kind") or "unknown"
        body = (item.get("content") or "").strip()
        body_render = body if body else "(no content body — do not fabricate; note in caveats)"
        item_blocks.append(
            f"### Item {i}: {title}  (kind: {kind})\n{body_render}"
        )
    items_block = "\n\n".join(item_blocks) or "(no content items supplied)"

    ceiling = f"\nTARGET BLOOM CEILING: {bloom_ceiling}" if bloom_ceiling else ""

    return (
        f"MODULE NUMBER: {module_number}\n"
        f"MODULE TITLE: {module_title}\n"
        f"MODULE ID (slug): {module_id}{ceiling}\n\n"
        "LEARNING OBJECTIVES (the spine of the deck — every concept slide must "
        "serve one of these):\n"
        f"{objectives_block}\n\n"
        "COURSE CONTENT ITEMS (the substance — ground the slides, code, and "
        "examples strictly in these bodies):\n"
        f"{items_block}\n\n"
        "Author the complete Marp `deck.md` for this module now. Ground "
        "everything in the objectives and content above; surface any gap in "
        "`caveats` rather than inventing content."
        f"{_generate_in(language)}"
    )
