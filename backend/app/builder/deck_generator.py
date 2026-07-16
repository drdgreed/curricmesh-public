"""Deck-from-a-course orchestrator (Slide System Port slice 2 — S2).

Composes the governed ``DeckGenerator`` seam (``app/ai/deckgen.py``) over a draft
course's real content: its learning objectives (the deck's spine) and its content
items (the substance). Produces a standard-conforming Marp ``deck.md`` that the S1
pipeline renders to PDF/PPTX/HTML.

The result is ADVISORY (D-2): the deck is fully AI-generated but a HUMAN reviews it
before release. This orchestrator therefore RETURNS the generated deck for review —
it writes NOTHING into the draft model (no migration, no auto-publish). The mandatory
QA -> approval -> release gate still stands between any artifact and an active version.

Grounding discipline (freshness pipeline): the deck is grounded strictly in the
course's objectives + item bodies; the seam's system prompt forbids inventing facts.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deckgen import DeckGenerator
from app.ai.schemas import GeneratedDeck
from app.builder.models import DraftCourse, DraftItem, DraftObjective


def _slugify(title: str) -> str:
    """A url-safe module slug derived from a course title (matches compile._slugify)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "course"


async def generate_deck_for_course(
    session: AsyncSession,
    course_id: uuid.UUID,
    author_ai: DeckGenerator,
) -> GeneratedDeck | None:
    """Author a Marp ``deck.md`` for a draft course. ADVISORY — returned, not stored.

    Loads the course tenant-scoped (a cross-org/unknown id is invisible through RLS
    and yields ``None`` — the caller maps that to 404), gathers its objectives
    (ordered) and content items (ordered by week then position), and hands them to
    the governed ``DeckGenerator``. Returns the generated deck for human review.

    A course with no objectives/items still generates: the seam degrades to a
    standard-conforming skeleton and records the gaps in the deck's ``caveats`` —
    it never fabricates content to fill the gap.
    """
    course = (
        await session.execute(select(DraftCourse).where(DraftCourse.id == course_id))
    ).scalar_one_or_none()
    if course is None:
        return None

    objective_rows = (
        await session.execute(
            select(DraftObjective)
            .where(DraftObjective.draft_course_id == course_id)
            .order_by(DraftObjective.order_index)
        )
    ).scalars().all()
    objectives = [
        {"text": o.text, "bloom_level": o.bloom_level} for o in objective_rows
    ]

    item_rows = (
        await session.execute(
            select(DraftItem)
            .where(DraftItem.draft_course_id == course_id)
            .order_by(DraftItem.week_index, DraftItem.order_index)
        )
    ).scalars().all()
    items = [
        {
            "title": it.title,
            "kind": it.kind.value if hasattr(it.kind, "value") else str(it.kind),
            "content": it.content,
        }
        for it in item_rows
    ]

    return await author_ai.generate_deck(
        module_title=course.title,
        # Draft courses have no module number; the seam handles an empty number
        # (uses a neutral placeholder + a caveat) rather than inventing one.
        module_number="",
        module_id=_slugify(course.title),
        objectives=objectives,
        items=items,
    )
