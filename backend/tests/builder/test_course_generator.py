"""Authoring Platform slice 4 — full-course-from-a-brief orchestrator.

Drives :func:`app.builder.course_generator.generate_course` against a freshly
created, RLS-enabled schema under an org context, injecting a FAKE
``CourseAuthorAI`` — ZERO real Anthropic calls in CI.

Harness (``rls_engine`` / ``_org_and_user`` / ``_open_org_session``) mirrors
tests/authoring_ai/test_authoring_ai_api.py.

Asserted:
  * happy path — N objectives -> N DraftObjectives, ~2N DraftItems
    (lesson + assessment each), correct week distribution, alignments wired.
  * best-effort — a generator whose one call raises -> that item is skipped,
    a DraftAdvisorNote records it, and the course is still assembled.
  * the distribution helper is a pure, even, deterministic spread.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedItemContent,
    GeneratedObjective,
    GeneratedObjectives,
)
from app.builder.course_generator import (
    CourseBrief,
    distribute_objectives_across_weeks,
    generate_course,
)
from app.builder.router_course import update_item, update_objective
from app.builder.schemas import ItemUpdate, ObjectiveUpdate
from app.builder.models import (
    DraftAdvisorNote,
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
async def rls_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _org_and_user(engine) -> tuple[uuid.UUID, uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Org A")
        s.add(org)
        await s.flush()
        user = User(
            email="course-gen@example.com", role="instructor", organization_id=org.id
        )
        s.add(user)
        await s.commit()
        return org.id, user.id


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Fake CourseAuthorAI
# ---------------------------------------------------------------------------


class FakeAuthorAI:
    """Returns ``count`` canned objectives + a lesson/assessment per call."""

    def __init__(self, count: int = 4) -> None:
        self.count = count
        self.calls: dict[str, int] = {"objectives": 0, "content": 0, "assessment": 0}
        # T3a — record the language each per-aspect generator was asked for, so a
        # test can assert the brief's language reaches every generator call.
        self.languages: dict[str, list[str]] = {
            "objectives": [], "content": [], "assessment": []
        }

    async def generate_objectives(
        self, *, topic: str, learner_profile: dict, count: int = 5,
        language: str = "en",
    ) -> GeneratedObjectives:
        self.calls["objectives"] += 1
        self.languages["objectives"].append(language)
        return GeneratedObjectives(
            objectives=[
                GeneratedObjective(
                    text=f"Objective {i} for {topic}",
                    bloom_level="apply",
                    key_skills=[f"skill-{i}"],
                )
                for i in range(self.count)
            ]
        )

    async def generate_item_content(
        self, *, objective: str, kind: str, course_context: str,
        language: str = "en",
    ) -> GeneratedItemContent:
        self.calls["content"] += 1
        self.languages["content"].append(language)
        return GeneratedItemContent(
            kind=kind,
            content_markdown=f"# Lesson for {objective}",
            summary="A lesson.",
            caveats=["verify me"],
        )

    async def generate_assessment(
        self, *, objective: str, course_context: str, language: str = "en",
    ) -> GeneratedAssessment:
        self.calls["assessment"] += 1
        self.languages["assessment"].append(language)
        return GeneratedAssessment(
            content_markdown=f"## Quiz for {objective}",
            rubric="rubric md",
            caveats=[],
        )


class FailingLessonAuthorAI(FakeAuthorAI):
    """Like FakeAuthorAI but every lesson (item-content) generation raises."""

    async def generate_item_content(
        self, *, objective: str, kind: str, course_context: str,
        language: str = "en",
    ) -> GeneratedItemContent:
        raise RuntimeError("model refused the lesson")


# ---------------------------------------------------------------------------
# Distribution helper (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_obj,n_weeks,expected",
    [
        (5, 5, [1, 2, 3, 4, 5]),
        (4, 2, [1, 1, 2, 2]),
        (3, 2, [1, 1, 2]),
        (2, 5, [1, 3]),
        (6, 3, [1, 1, 2, 2, 3, 3]),
        (1, 4, [1]),
        (0, 4, []),
    ],
)
def test_distribute_objectives_across_weeks(n_obj, n_weeks, expected):
    assert distribute_objectives_across_weeks(n_obj, n_weeks) == expected


def test_distribution_is_bounded_and_full_length():
    """For any valid N/W: length == N, values within [1, W], and non-decreasing."""
    for n_obj in range(1, 21):
        for n_weeks in range(1, 13):
            weeks = distribute_objectives_across_weeks(n_obj, n_weeks)
            assert len(weeks) == n_obj
            assert all(1 <= w <= n_weeks for w in weeks)
            assert weeks == sorted(weeks)  # sequential / non-decreasing


# ---------------------------------------------------------------------------
# Orchestrator — happy path
# ---------------------------------------------------------------------------


async def test_generate_course_assembles_full_draft(rls_engine):
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="AI Engineering 101",
                    topic="Building tool-using agents",
                    learner_profile={"experience_level": "mid"},
                    target_weeks=4,
                    objectives_count=4,
                )
                fake = FakeAuthorAI(count=4)
                result = await generate_course(
                    session, brief=brief, author_ai=fake, author_id=user_id
                )

                # N objectives, 2N items (lesson + assessment each), no skips.
                assert result.objectives_count == 4
                assert result.items_count == 8
                assert result.skipped == []
                assert fake.calls == {"objectives": 1, "content": 4, "assessment": 4}

                course_id = result.course.id
                assert result.course.title == "AI Engineering 101"
                assert result.course.created_by == user_id

                objectives = (
                    await session.execute(
                        select(DraftObjective)
                        .where(DraftObjective.draft_course_id == course_id)
                        .order_by(DraftObjective.order_index)
                    )
                ).scalars().all()
                assert len(objectives) == 4
                # Even weekly distribution across 4 weeks.
                assert [o.week_index for o in objectives] == [1, 2, 3, 4]
                assert objectives[0].bloom_level == "apply"
                assert objectives[0].key_skills == {"skills": ["skill-0"]}

                items = (
                    await session.execute(
                        select(DraftItem).where(DraftItem.draft_course_id == course_id)
                    )
                ).scalars().all()
                assert len(items) == 8
                kinds = sorted(i.kind for i in items)
                assert kinds.count(AssetKind.lesson_plan) == 4
                assert kinds.count(AssetKind.assessment) == 4
                # Item weeks mirror their objective's week.
                for item in items:
                    assert item.week_index in {1, 2, 3, 4}

                # Every item is aligned to exactly one objective.
                align_count = (
                    await session.execute(select(func.count()).select_from(DraftItemObjective))
                ).scalar_one()
                assert align_count == 8
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_course_threads_brief_language_into_every_generator(rls_engine):
    """T3a: ``brief.language`` reaches objectives + every lesson + every assessment
    generator call. The default ``en`` is threaded unchanged when unspecified."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                # Non-default language: it must reach all 1 + 2N generator calls.
                brief = CourseBrief(
                    title="Maestría en IA Agéntica",
                    topic="Construir agentes con herramientas",
                    learner_profile={"experience_level": "mid"},
                    target_weeks=3,
                    objectives_count=3,
                    language="Spanish",
                )
                fake = FakeAuthorAI(count=3)
                await generate_course(
                    session, brief=brief, author_ai=fake, author_id=user_id
                )
                assert fake.languages["objectives"] == ["Spanish"]
                assert fake.languages["content"] == ["Spanish"] * 3
                assert fake.languages["assessment"] == ["Spanish"] * 3

                # Default brief (no language) threads the "en" default everywhere.
                default_brief = CourseBrief(
                    title="AI Engineering 101",
                    topic="Building tool-using agents",
                    learner_profile={},
                    target_weeks=2,
                    objectives_count=2,
                )
                assert default_brief.language == "en"
                fake2 = FakeAuthorAI(count=2)
                await generate_course(
                    session, brief=default_brief, author_ai=fake2, author_id=user_id
                )
                assert fake2.languages["objectives"] == ["en"]
                assert fake2.languages["content"] == ["en", "en"]
                assert fake2.languages["assessment"] == ["en", "en"]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_course_best_effort_skips_failed_item(rls_engine):
    """One generator call raising skips that item — the course is still assembled."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="Course",
                    topic="Topic",
                    target_weeks=3,
                    objectives_count=3,
                )
                fake = FailingLessonAuthorAI(count=3)
                result = await generate_course(
                    session, brief=brief, author_ai=fake, author_id=user_id
                )

                # Objectives still created; lessons all skipped; assessments kept.
                assert result.objectives_count == 3
                assert result.items_count == 3  # 3 assessments only
                assert len(result.skipped) == 3
                assert all(s.kind == "lesson_plan" for s in result.skipped)

                course_id = result.course.id
                items = (
                    await session.execute(
                        select(DraftItem).where(DraftItem.draft_course_id == course_id)
                    )
                ).scalars().all()
                assert len(items) == 3
                assert all(i.kind == AssetKind.assessment for i in items)

                # Objectives are all present — a coherent partial course.
                objectives = (
                    await session.execute(
                        select(DraftObjective).where(
                            DraftObjective.draft_course_id == course_id
                        )
                    )
                ).scalars().all()
                assert len(objectives) == 3

                # Each skip is recorded as a durable advisor note.
                notes = (
                    await session.execute(
                        select(DraftAdvisorNote).where(
                            DraftAdvisorNote.draft_course_id == course_id
                        )
                    )
                ).scalars().all()
                assert len(notes) == 3
                assert all(n.kind == "warning" for n in notes)
                assert all("lesson_plan" in n.text for n in notes)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_course_distributes_uneven_counts(rls_engine):
    """More objectives than weeks -> objectives stack into weeks, still bounded."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="Dense Course", topic="T", target_weeks=2, objectives_count=5
                )
                fake = FakeAuthorAI(count=5)
                result = await generate_course(
                    session, brief=brief, author_ai=fake, author_id=user_id
                )
                objectives = (
                    await session.execute(
                        select(DraftObjective)
                        .where(DraftObjective.draft_course_id == result.course.id)
                        .order_by(DraftObjective.order_index)
                    )
                ).scalars().all()
                # 5 objectives / 2 weeks -> even split, remainder front-loaded (3 + 2).
                assert [o.week_index for o in objectives] == [1, 1, 1, 2, 2]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generated_course_is_mutable_via_builder_endpoints(rls_engine):
    """ACCEPTANCE: a brief -> a fully-populated draft the author can then EDIT.

    Proves the orchestrator's output is a genuine mutable draft, not a frozen
    artifact: after generation, edits through the ordinary builder endpoints
    (update_objective / update_item) persist.
    """
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="AI Engineering 101",
                    topic="Building tool-using agents",
                    learner_profile={"experience_level": "mid"},
                    target_weeks=3,
                    objectives_count=3,
                )
                result = await generate_course(
                    session, brief=brief, author_ai=FakeAuthorAI(count=3), author_id=user_id
                )
                course_id = result.course.id

                # Fully populated: objectives + lessons + assessments + alignments.
                objectives = (
                    await session.execute(
                        select(DraftObjective).where(
                            DraftObjective.draft_course_id == course_id
                        )
                    )
                ).scalars().all()
                items = (
                    await session.execute(
                        select(DraftItem).where(DraftItem.draft_course_id == course_id)
                    )
                ).scalars().all()
                aligns = (
                    await session.execute(
                        select(DraftItemObjective).join(
                            DraftItem,
                            DraftItem.id == DraftItemObjective.draft_item_id,
                        ).where(DraftItem.draft_course_id == course_id)
                    )
                ).scalars().all()
                assert len(objectives) == 3
                assert sum(1 for i in items if i.kind == AssetKind.lesson_plan) == 3
                assert sum(1 for i in items if i.kind == AssetKind.assessment) == 3
                assert len(aligns) == 6

                # --- Author edits the draft through the existing builder endpoints ---
                obj = objectives[0]
                await update_objective(
                    obj.id,
                    ObjectiveUpdate(text="Author-revised objective"),
                    current=current,
                    db=session,
                )
                lesson = next(i for i in items if i.kind == AssetKind.lesson_plan)
                await update_item(
                    lesson.id,
                    ItemUpdate(content="Author-rewritten lesson body"),
                    current=current,
                    db=session,
                )

                # The edits persisted — the generated draft is truly mutable.
                await session.refresh(obj)
                await session.refresh(lesson)
                assert obj.text == "Author-revised objective"
                assert lesson.content == "Author-rewritten lesson body"
            finally:
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# Progress callback (Task 2 — async background runner hook)
# ---------------------------------------------------------------------------


async def test_generate_course_reports_progress(rls_engine):
    """on_progress is called once per unit of work: 1 (objectives) + 2N (items)."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="Progress course",
                    topic="Progress reporting",
                    target_weeks=3,
                    objectives_count=3,
                )
                seen: list[tuple[int, int, str]] = []

                async def on_progress(completed: int, total: int, phase: str) -> None:
                    seen.append((completed, total, phase))

                result = await generate_course(
                    session,
                    brief=brief,
                    author_ai=FakeAuthorAI(count=3),
                    author_id=user_id,
                    on_progress=on_progress,
                )
                assert result.objectives_count == 3

                # total = 1 + 2*3 = 7 units; called that many times, monotonic.
                assert len(seen) == 7
                assert all(total == 7 for _, total, _ in seen)
                completeds = [c for c, _, _ in seen]
                assert completeds == [1, 2, 3, 4, 5, 6, 7]
                assert completeds == sorted(completeds)
                # First step is the objectives phase; last reaches total.
                assert "objective" in seen[0][2].lower()
                assert seen[-1][0] == 7
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_course_progress_counts_skipped_items(rls_engine):
    """A skipped (failed) lesson still advances the step — progress reflects work
    attempted, and the callback still fires 1 + 2N times."""
    engine = rls_engine
    org_a, user_id = await _org_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                brief = CourseBrief(
                    title="Skip course",
                    topic="Best effort",
                    target_weeks=2,
                    objectives_count=2,
                )
                seen: list[int] = []

                async def on_progress(completed: int, total: int, phase: str) -> None:
                    seen.append(completed)

                result = await generate_course(
                    session,
                    brief=brief,
                    author_ai=FailingLessonAuthorAI(count=2),  # every lesson raises
                    author_id=user_id,
                    on_progress=on_progress,
                )
                # Lessons skipped but course still assembled (assessments only).
                assert result.items_count == 2
                assert len(result.skipped) == 2
                # Still 1 + 2*2 = 5 progress calls, monotonic to total.
                assert seen == [1, 2, 3, 4, 5]
            finally:
                await session.close()
    finally:
        current_org.reset(token)
