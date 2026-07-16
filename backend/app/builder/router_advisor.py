"""Router: /api/v1/builder — AI co-pilot endpoints (Phase 2, Task 1).

Four advisory endpoints:
  1. POST /items/{item_id}/categorize-ai  — stateless item classification.
  2. POST /courses/{course_id}/advise      — andragogy notes (persisted).
  3. GET  /courses/{course_id}/advisor-notes — list persisted notes.
  4. PATCH /advisor-notes/{note_id}         — flip a note status.

Testability mirrors ``app/routers/impact.py``: ``get_categorizer`` and
``get_advisor`` raise 503 when no API key is configured; tests pass a fake
directly to the handler — ZERO real Anthropic calls in CI.

Write order mirrors router_course.py: ``db.add → await db.flush() →
await db.refresh() → build out → await db.commit()``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.builder_advisor import AndragogyAdvisor, CategorizeResult, Categorizer, PrereqInferer, _clip
from app.ai.client import AIClient
from app.auth.rbac import require_roles
from app.builder.models import (
    DraftAdvisorNote,
    DraftDependency,
    DraftItem,
    DraftObjective,
    DraftCourse,
)
from app.builder.graph_utils import would_create_cycle
from app.builder.router_course import _get_course, _get_item
from app.builder.schemas import (
    AdvisorNoteOut,
    AdvisorNoteStatusUpdate,
    AdviseRequest,
    InferDepsResult,
)
from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/v1/builder", tags=["builder-ai"])

# Mirror router_course.py — the same tier of authors drive both routers.
_AUTHOR_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)


# ---------------------------------------------------------------------------
# Dependencies (503 without key; tests pass a fake directly)
# ---------------------------------------------------------------------------


def get_categorizer() -> Categorizer:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency by passing a fake analyzer as the
    ``analyzer=`` keyword arg to the handler — ZERO real network in CI.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI item categorization is not configured (ANTHROPIC_API_KEY missing)"
            ),
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


def get_advisor() -> AndragogyAdvisor:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency by passing a fake advisor as the
    ``analyzer=`` keyword arg to the handler — ZERO real network in CI.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI andragogy advising is not configured (ANTHROPIC_API_KEY missing)"
            ),
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


def get_prereq_inferer() -> PrereqInferer:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency by passing a fake inferer as the
    ``inferer=`` keyword arg to the handler — ZERO real network in CI.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI prerequisite inference is not configured (ANTHROPIC_API_KEY missing)"
            ),
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Context builder (stateless — no DB writes)
# ---------------------------------------------------------------------------


async def _build_course_context(
    db: AsyncSession, course: DraftCourse
) -> str:
    """Build a compact, model-readable course-context string.

    Includes: course title + description, each objective (text + bloom +
    week), and items grouped by week (title + kind). Structured but terse —
    just enough signal for the andragogy advisor to reason without burning
    too many tokens.
    """
    lines: list[str] = [
        f"COURSE: {_clip(course.title, 200)}",
    ]
    if course.description:
        lines.append(f"DESCRIPTION: {_clip(course.description, 500)}")
    if course.target_weeks:
        lines.append(f"TARGET WEEKS: {course.target_weeks}")

    # Objectives.
    objectives = (
        await db.execute(
            select(DraftObjective)
            .where(DraftObjective.draft_course_id == course.id)
            .order_by(
                DraftObjective.week_index.nulls_last(),
                DraftObjective.order_index,
            )
        )
    ).scalars().all()

    if objectives:
        lines.append("\nOBJECTIVES:")
        for obj in objectives:
            week = f"week {obj.week_index}" if obj.week_index is not None else "unscheduled"
            lines.append(f"  - [{obj.bloom_level}] {_clip(obj.text, 300)} ({week})")
    else:
        lines.append("\nOBJECTIVES: (none yet)")

    # Items grouped by week.
    items = (
        await db.execute(
            select(DraftItem)
            .where(DraftItem.draft_course_id == course.id)
            .order_by(
                DraftItem.week_index.nulls_last(),
                DraftItem.order_index,
            )
        )
    ).scalars().all()

    if items:
        lines.append("\nITEMS (by week):")
        current_week: int | None = object()  # sentinel — never matches
        for item in items:
            w = item.week_index
            if w != current_week:
                week_label = f"Week {w}" if w is not None else "Unscheduled"
                lines.append(f"  {week_label}:")
                current_week = w
            kind_str = item.kind.value if hasattr(item.kind, "value") else str(item.kind)
            mins = f" ({item.estimated_minutes} min)" if item.estimated_minutes else ""
            lines.append(f"    - {_clip(item.title, 200)} [{kind_str}]{mins}")
    else:
        lines.append("\nITEMS: (none yet)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/items/{item_id}/categorize-ai", response_model=CategorizeResult)
async def categorize_item_ai(
    item_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    analyzer: Categorizer = Depends(get_categorizer),
) -> CategorizeResult:
    """Stateless AI classification of a draft item. Advisory — does NOT mutate the item.

    Loads the item's course objectives and asks the AI to classify the item
    into an asset kind, estimate student-facing minutes, assign a complexity
    multiplier, and identify the best-serving objective. The result is returned
    to the caller for client-side application; the item row is not touched.
    """
    item = await _get_item(db, item_id)

    # Load the objectives for the item's course so the model has grounding.
    objectives_rows = (
        await db.execute(
            select(DraftObjective.text).where(
                DraftObjective.draft_course_id == item.draft_course_id
            )
        )
    ).scalars().all()

    return await analyzer.categorize(
        title=item.title,
        content=item.content,
        objectives=list(objectives_rows),
    )


@router.post(
    "/courses/{course_id}/advise",
    response_model=list[AdvisorNoteOut],
)
async def advise_course(
    course_id: uuid.UUID,
    body: AdviseRequest,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    analyzer: AndragogyAdvisor = Depends(get_advisor),
) -> list[AdvisorNoteOut]:
    """Run the andragogy advisor and persist each returned note.

    Builds a compact course-context string (objectives + items by week),
    passes it to the advisor alongside the course's learner_profile and the
    optional focus hint, then persists each returned AdviceItem as a
    DraftAdvisorNote. Returns the saved note list.
    """
    course = await _get_course(db, course_id)

    course_context = await _build_course_context(db, course)
    learner_profile: dict = course.learner_profile or {}

    report = await analyzer.advise(
        course_context=course_context,
        learner_profile=learner_profile,
        focus=body.focus,
    )

    # Persist each note (flush+refresh each so we can build AdvisorNoteOut
    # before committing — mirrors the write pattern in router_course.py).
    out_list: list[AdvisorNoteOut] = []
    for note in report.notes:
        row = DraftAdvisorNote(
            draft_course_id=course_id,
            target_kind=note.target_kind,
            target_ref=note.target_ref,
            kind=note.kind,
            text=note.text,
            status="open",
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        out_list.append(AdvisorNoteOut.model_validate(row))

    await db.commit()
    return out_list


@router.get(
    "/courses/{course_id}/advisor-notes",
    response_model=list[AdvisorNoteOut],
)
async def list_advisor_notes(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> list[AdvisorNoteOut]:
    """List all persisted advisor notes for a draft course, ordered by created_at."""
    await _get_course(db, course_id)  # 404 if missing

    rows = (
        await db.execute(
            select(DraftAdvisorNote)
            .where(DraftAdvisorNote.draft_course_id == course_id)
            .order_by(DraftAdvisorNote.created_at)
        )
    ).scalars().all()

    return [AdvisorNoteOut.model_validate(r) for r in rows]


@router.patch(
    "/advisor-notes/{note_id}",
    response_model=AdvisorNoteOut,
)
async def update_advisor_note_status(
    note_id: uuid.UUID,
    body: AdvisorNoteStatusUpdate,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
) -> AdvisorNoteOut:
    """Update the status of an advisor note (accepted or dismissed)."""
    note = (
        await db.execute(
            select(DraftAdvisorNote).where(DraftAdvisorNote.id == note_id)
        )
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Advisor note not found")

    note.status = body.status
    db.add(note)
    await db.flush()
    await db.refresh(note)
    out = AdvisorNoteOut.model_validate(note)
    await db.commit()
    return out


@router.post(
    "/courses/{course_id}/infer-deps",
    response_model=InferDepsResult,
)
async def infer_deps(
    course_id: uuid.UUID,
    current: dict[str, Any] = Depends(_AUTHOR_ROLES),
    db: AsyncSession = Depends(get_db),
    inferer: PrereqInferer = Depends(get_prereq_inferer),
) -> InferDepsResult:
    """Run the prerequisite inferer and persist AI-suggested dependency edges.

    Loads all items for the course, calls the PrereqInferer, then for each
    suggestion: resolves titles to real item IDs, skips cycles/duplicates/
    unresolvable titles, and inserts surviving edges as
    DraftDependency(source="ai_suggested", accepted=False).

    For each missing-dependency flag, persists a DraftAdvisorNote with
    kind="warning" and status="open".

    Returns counts of created dependencies and flagged notes.

    NOTE: if two items share the same title, the first one wins for resolution
    purposes.
    """
    await _get_course(db, course_id)  # 404 if missing

    # Load all items for the course and build a title → item map (first wins on dupe).
    item_rows = (
        await db.execute(
            select(DraftItem)
            .where(DraftItem.draft_course_id == course_id)
            .order_by(DraftItem.order_index, DraftItem.id)
        )
    ).scalars().all()

    title_map: dict[str, DraftItem] = {}
    for item in item_rows:
        if item.title not in title_map:
            title_map[item.title] = item

    # Build the items list for the inferer.
    items_payload = [
        {
            "title": item.title,
            "kind": item.kind.value if hasattr(item.kind, "value") else str(item.kind),
            "week": item.week_index,
        }
        for item in item_rows
    ]

    # Call the inferer.
    report = await inferer.infer(items=items_payload)

    # Load all existing edges for this course (used for duplicate-skip and
    # cycle detection).
    existing_rows = (
        await db.execute(
            select(DraftDependency.from_item_id, DraftDependency.to_item_id).where(
                DraftDependency.draft_course_id == course_id
            )
        )
    ).all()
    existing_set: set[tuple] = {(r.from_item_id, r.to_item_id) for r in existing_rows}

    # Accepted-only edges for cycle checking (mirror create_dependency).
    accepted_rows = (
        await db.execute(
            select(DraftDependency.from_item_id, DraftDependency.to_item_id).where(
                DraftDependency.draft_course_id == course_id,
                DraftDependency.accepted == True,  # noqa: E712
            )
        )
    ).all()
    # In-memory list of (from_id, to_id) pairs used solely for intra-batch
    # cycle detection. Seeded from pre-existing accepted edges; each inserted
    # ai_suggested edge is appended so a contradictory intra-batch pair
    # (e.g. A→B then B→A) is blocked. Rows are inserted with accepted=False
    # regardless — this list is a cycle-guard only, not an acceptance signal.
    accepted_edges: list[tuple] = [(r.from_item_id, r.to_item_id) for r in accepted_rows]

    suggested_created = 0
    for suggestion in report.suggested:
        from_item = title_map.get(suggestion.from_title)
        to_item = title_map.get(suggestion.to_title)

        # Skip unresolvable titles.
        if from_item is None or to_item is None:
            continue

        from_id = from_item.id
        to_id = to_item.id

        # Skip self-loops.
        if from_id == to_id:
            continue

        # Skip already-existing edges (any source, accepted or not).
        if (from_id, to_id) in existing_set:
            continue

        # Skip cycle-creating suggestions (check against accepted edges only,
        # matching the behaviour of create_dependency).
        if would_create_cycle(accepted_edges, from_id, to_id):
            continue

        dep = DraftDependency(
            draft_course_id=course_id,
            from_item_id=from_id,
            to_item_id=to_id,
            edge_type="prerequisite",
            source="ai_suggested",
            accepted=False,
        )
        db.add(dep)
        existing_set.add((from_id, to_id))
        # Append to the in-memory cycle-guard so subsequent suggestions in the
        # same batch cannot form a contradiction (e.g. if A→B is inserted here,
        # a later B→A suggestion will be caught by would_create_cycle above).
        # Rows remain accepted=False — this append is for cycle detection only.
        accepted_edges.append((from_id, to_id))
        suggested_created += 1

    missing_flagged = 0
    for missing in report.missing:
        matched_item = title_map.get(missing.item_title)
        # target_ref is the matched DraftItem UUID (not a free-text ref like
        # andragogy advise notes) — the frontend should treat it as an item id.
        target_ref = str(matched_item.id) if matched_item is not None else None

        note = DraftAdvisorNote(
            draft_course_id=course_id,
            target_kind="item",
            target_ref=target_ref,
            kind="warning",
            text=f"{missing.item_title} needs {missing.needs}: {missing.reason}",
            status="open",
        )
        db.add(note)
        missing_flagged += 1

    await db.flush()
    await db.commit()
    return InferDepsResult(
        suggested_created=suggested_created,
        missing_flagged=missing_flagged,
    )
