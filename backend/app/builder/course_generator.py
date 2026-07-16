"""Full-course-from-a-brief orchestrator (Authoring Platform slice 4 — the headline).

Turns a :class:`CourseBrief` into a complete, MUTABLE ``DraftCourse`` by
orchestrating the per-aspect generators of the governed ``CourseAuthorAI`` seam
(slice 3): objectives -> a deterministic weekly distribution -> a lesson + an
assessment ``DraftItem`` per objective -> alignments. The assembled draft is a
*first draft* the author then refines through the existing builder endpoints; it
never bypasses the human or the mandatory QA -> approval -> release gate.

Design decisions (see ``docs/specs/2026-07-06-authoring-platform-phase1-design.md`` §B):

* **Governed AI only.** Every generation goes through the injected
  ``CourseAuthorAI`` (the real ``AIClient`` in prod, a fake in tests) — never a
  raw model call.
* **Cost is bounded at the brief.** ``objectives_count`` (<= 20) is the cost
  governor: the number of AI calls is ``1 + 2 * (objectives actually drafted)``.
* **Deterministic weekly distribution.** :func:`distribute_objectives_across_weeks`
  is a pure, unit-tested helper — the same brief always lays objectives out the
  same way.
* **Per-item generation is best-effort.** One failed lesson/assessment generation
  must NOT sink the whole course: the failing item is skipped, a durable
  ``DraftAdvisorNote`` records why, and assembly continues. A brief always yields
  a coherent (possibly partial) draft the author can finish by hand.
* **One transaction.** All AI content is gathered first (no DB writes); the course
  + objectives + items + alignments + skip-notes are then assembled and committed
  once.

No new table, no migration — everything lands in the existing draft model.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

# Progress callback: ``(completed_steps, total_steps, phase_label)``. Called as
# each unit of work completes so a long run can report progress out-of-band
# (the async background runner persists it onto a GenerationJob row). ``total``
# is ``1 + 2 * objectives_drafted`` — one step for objectives, then a lesson +
# an assessment step per objective.
OnProgress = Callable[[int, int, str], Awaitable[None]]

from app.ai.client import CourseAuthorAI
from app.builder.models import (
    DraftAdvisorNote,
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.models.enums import AssetKind

# The asset kind requested from the content generator for a per-objective lesson.
_LESSON_KIND = AssetKind.lesson_plan
_ASSESSMENT_KIND = AssetKind.assessment

# DraftItem.title is String(512); keep generated titles inside that bound.
_TITLE_MAX = 512


class CourseBrief(BaseModel):
    """The author's request for a full generated draft course.

    ``objectives_count`` is the cost governor — it caps how many AI generations
    the orchestrator makes (``1 + 2 * count`` in the worst case). It is bounded
    to <= 20 so a single brief can never fan out into an unbounded, expensive run.
    """

    title: str = Field(description="Working title for the draft course.")
    topic: str = Field(description="The subject the objectives are generated for.")
    learner_profile: dict = Field(
        default_factory=dict,
        description="Who the course is for — grounds objective generation.",
    )
    target_weeks: int = Field(
        ge=1, le=52, description="How many weeks to distribute the objectives across."
    )
    objectives_count: int = Field(
        ge=1,
        le=20,
        description="How many objectives to draft. THE COST BOUND (<= 20).",
    )
    hours_per_week: float | None = Field(
        default=None, description="Optional weekly effort target (grounding only)."
    )
    language: str = Field(
        default="en",
        description=(
            "Target language for the GENERATED content (a plain language name or "
            "BCP-47 tag, e.g. 'Spanish' / 'es'). Default 'en' preserves the "
            "current English-only behaviour. Threaded into every per-aspect "
            "generator prompt; separate from the tutor's session language (T3)."
        ),
    )


@dataclass
class SkippedItem:
    """A per-objective item generation that failed and was skipped (best-effort)."""

    objective_index: int
    objective_text: str
    kind: str
    error: str


@dataclass
class CourseGenerationResult:
    """The outcome of :func:`generate_course`.

    ``course`` is the assembled (refreshed) ``DraftCourse``; ``objectives_count``
    and ``items_count`` are what actually landed; ``skipped`` is the list of
    lesson/assessment generations that failed and were skipped.
    """

    course: DraftCourse
    objectives_count: int
    items_count: int
    skipped: list[SkippedItem] = field(default_factory=list)


def distribute_objectives_across_weeks(
    n_objectives: int, n_weeks: int
) -> list[int]:
    """Spread ``n_objectives`` across ``n_weeks`` deterministically and evenly.

    Pure helper (no I/O). Returns a list of length ``n_objectives`` giving the
    1-based ``week_index`` for each objective, in order. Objectives are laid out
    sequentially and as evenly as the counts allow — the same inputs always yield
    the same layout. The result is always within ``[1, n_weeks]``.

    Examples::

        (5, 5) -> [1, 2, 3, 4, 5]   # one per week
        (4, 2) -> [1, 1, 2, 2]      # two per week
        (3, 2) -> [1, 1, 2]         # front-loaded remainder
        (2, 5) -> [1, 3]            # spread out
        (0, 4) -> []                # nothing to place
    """
    if n_objectives <= 0:
        return []
    if n_weeks < 1:
        n_weeks = 1
    return [1 + (i * n_weeks) // n_objectives for i in range(n_objectives)]


def _brief_context(brief: CourseBrief) -> str:
    """A compact, model-readable course-context string for grounding the generators."""
    lines = [f"COURSE: {brief.title}", f"TOPIC: {brief.topic}"]
    lines.append(f"TARGET WEEKS: {brief.target_weeks}")
    if brief.hours_per_week is not None:
        lines.append(f"HOURS PER WEEK: {brief.hours_per_week}")
    return "\n".join(lines)


def _clip_title(text: str, prefix: str) -> str:
    """Build ``{prefix}{objective text}`` clipped to the DraftItem.title bound."""
    title = f"{prefix}{text}"
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1].rstrip() + "…"
    return title


async def generate_course(
    session: AsyncSession,
    *,
    brief: CourseBrief,
    author_ai: CourseAuthorAI,
    author_id: uuid.UUID | None,
    on_progress: OnProgress | None = None,
) -> CourseGenerationResult:
    """Orchestrate the per-aspect generators into a complete, mutable DraftCourse.

    Flow: draft objectives -> distribute across ``target_weeks`` -> per objective
    generate a lesson + an assessment (best-effort) -> assemble the course +
    objectives + items + alignments (+ skip notes) in one transaction.

    Best-effort: a failed lesson/assessment generation is skipped and recorded as
    a ``DraftAdvisorNote``; the course is still assembled. A brief that produces
    zero usable items still yields a coherent draft (objectives only) the author
    can finish by hand.

    ``on_progress`` (optional): an async callback ``(completed, total, phase)``
    invoked as each unit of work lands — after objectives (step 1 of
    ``total = 1 + 2 * len(objectives)``) and after each objective's lesson and
    assessment attempts (best-effort, so a skipped item still advances the
    step). Used by the async background runner to persist live progress; when
    ``None`` the behaviour is identical to before (no-op), so existing callers
    are unaffected. Every progress call happens during the read/generate phases
    (before any DB writes), so a progress-persisting callback that commits only
    ever flushes its own bookkeeping row, never a partial course.
    """
    # --- Phase 1: draft objectives (bounded by objectives_count) ---
    generated = await author_ai.generate_objectives(
        topic=brief.topic,
        learner_profile=brief.learner_profile,
        count=brief.objectives_count,
        language=brief.language,
    )
    objectives = generated.objectives
    weeks = distribute_objectives_across_weeks(len(objectives), brief.target_weeks)
    context = _brief_context(brief)

    n = len(objectives)
    total_steps = 1 + 2 * n
    completed = 0

    async def _progress(phase: str) -> None:
        if on_progress is not None:
            await on_progress(completed, total_steps, phase)

    completed = 1
    await _progress(f"Drafted {n} objective{'s' if n != 1 else ''}")

    # --- Phase 2: gather per-objective content (best-effort, NO DB writes yet) ---
    # Each entry: (generated_objective, week_index, lesson_or_None, assessment_or_None)
    gathered: list[tuple] = []
    skipped: list[SkippedItem] = []
    for idx, obj in enumerate(objectives):
        lesson = None
        try:
            lesson = await author_ai.generate_item_content(
                objective=obj.text,
                kind=_LESSON_KIND.value,
                course_context=context,
                language=brief.language,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort: skip, never fail the course
            skipped.append(
                SkippedItem(
                    objective_index=idx,
                    objective_text=obj.text,
                    kind=_LESSON_KIND.value,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
        completed += 1
        await _progress(f"Lesson {idx + 1}/{n}")

        assessment = None
        try:
            assessment = await author_ai.generate_assessment(
                objective=obj.text,
                course_context=context,
                language=brief.language,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort: skip, never fail the course
            skipped.append(
                SkippedItem(
                    objective_index=idx,
                    objective_text=obj.text,
                    kind=_ASSESSMENT_KIND.value,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
        completed += 1
        await _progress(f"Assessment {idx + 1}/{n}")

        gathered.append((obj, weeks[idx], lesson, assessment))

    # --- Phase 3: assemble everything in ONE transaction ---
    course = DraftCourse(
        title=brief.title,
        description=brief.topic,
        learner_profile=brief.learner_profile or None,
        target_weeks=brief.target_weeks,
        status="drafting",
        created_by=author_id,
    )
    session.add(course)
    await session.flush()  # assign course.id

    items_count = 0
    order_index = 0
    for idx, (obj, week_index, lesson, assessment) in enumerate(gathered):
        draft_obj = DraftObjective(
            draft_course_id=course.id,
            text=obj.text,
            bloom_level=obj.bloom_level,
            key_skills={"skills": list(obj.key_skills)},
            week_index=week_index,
            order_index=idx,
        )
        session.add(draft_obj)
        await session.flush()  # assign draft_obj.id for alignments

        if lesson is not None:
            lesson_item = DraftItem(
                draft_course_id=course.id,
                kind=_LESSON_KIND,
                title=_clip_title(obj.text, "Lesson: "),
                content=lesson.content_markdown,
                week_index=week_index,
                order_index=order_index,
                ai_notes={"summary": lesson.summary, "caveats": list(lesson.caveats)},
            )
            session.add(lesson_item)
            await session.flush()
            session.add(
                DraftItemObjective(
                    draft_item_id=lesson_item.id, draft_objective_id=draft_obj.id
                )
            )
            items_count += 1
            order_index += 1

        if assessment is not None:
            assessment_item = DraftItem(
                draft_course_id=course.id,
                kind=_ASSESSMENT_KIND,
                title=_clip_title(obj.text, "Assessment: "),
                content=assessment.content_markdown,
                week_index=week_index,
                order_index=order_index,
                ai_notes={
                    "rubric": assessment.rubric,
                    "caveats": list(assessment.caveats),
                },
            )
            session.add(assessment_item)
            await session.flush()
            session.add(
                DraftItemObjective(
                    draft_item_id=assessment_item.id,
                    draft_objective_id=draft_obj.id,
                )
            )
            items_count += 1
            order_index += 1

    # Durable record of every skip, visible in the existing advisor-note surface.
    for skip in skipped:
        session.add(
            DraftAdvisorNote(
                draft_course_id=course.id,
                target_kind="objective",
                target_ref=str(skip.objective_index),
                kind="warning",
                text=(
                    f"Could not generate {skip.kind} for objective "
                    f"'{skip.objective_text}': {skip.error}. "
                    "Add this item by hand."
                ),
                status="open",
            )
        )

    await session.commit()
    await session.refresh(course)

    return CourseGenerationResult(
        course=course,
        objectives_count=len(objectives),
        items_count=items_count,
        skipped=skipped,
    )
