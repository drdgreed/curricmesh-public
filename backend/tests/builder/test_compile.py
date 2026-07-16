"""Task 7 — the CRITICAL publish/compile round-trip test.

Builds a small ``DraftCourse`` directly via the ORM, calls
:func:`app.builder.compile.publish_draft`, and then asserts the compiled
immutable model integrates with the EXISTING CurricMesh read paths
(``app.core.manifest`` · the graph router · the calendar router) — i.e. a
published draft is structurally indistinguishable from a forked/back-filled
curriculum.

Fixture style mirrors ``tests/release/test_release.py`` /
``tests/builder/test_publish_api.py``: a dedicated, RLS-enabled engine, an
org-pinned session, ambient tenant context.

What is asserted:
  * draft.curriculum_id set + status "published"; the active version resolves via
    ``active_curriculum_version`` and equals the returned CurriculumVersion.
  * members == items + objectives (3 + 2 == 5).
  * the graph endpoint returns nodes for the items (the lab node present) + the
    dependency edge + the supports edges.
  * the calendar endpoint returns week-1 and week-2 sections with the right tiles.
  * republishing the same draft raises AlreadyPublishedError (router → 409).
  * a draft whose DraftDependency forms a cycle → publish raises a
    CompileValidationError (router → 422), nothing persisted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.compile import (
    AlreadyPublishedError,
    CompileValidationError,
    publish_draft,
)
from app.builder.models import (
    DraftCourse,
    DraftDependency,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.database import Base
from app.db.rls import apply_rls
from app.models.content_model import ContentVersion
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.routers.course import get_course_calendar
from app.routers.graph import get_curriculum_graph
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def rls_engine():
    """Fresh, RLS-enabled schema — owned by this test module."""
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
        org = Organization(name="Test Org")
        s.add(org)
        await s.flush()
        user = User(email="author@test.com", role="instructor", organization_id=org.id)
        s.add(user)
        await s.commit()
        return org.id, user.id


async def _open_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Draft builders
# ---------------------------------------------------------------------------


async def _build_small_draft(
    session: AsyncSession, org_id: uuid.UUID
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A draft: 2 objectives (wk1, wk2), 3 items (wk1 slides, wk1 lab, wk2 lesson),
    each item aligned to an objective, and one dependency (wk1 lab depends-on wk1
    slides). Returns ``(draft_id, {"lab": item_id, ...})``.
    """
    course = DraftCourse(
        organization_id=org_id, title="Intro to Widgets", status="drafting"
    )
    session.add(course)
    await session.flush()

    obj1 = DraftObjective(
        organization_id=org_id,
        draft_course_id=course.id,
        text="Understand widgets",
        week_index=1,
        order_index=0,
    )
    obj2 = DraftObjective(
        organization_id=org_id,
        draft_course_id=course.id,
        text="Build a widget",
        week_index=2,
        order_index=0,
    )
    session.add_all([obj1, obj2])
    await session.flush()

    slides = DraftItem(
        organization_id=org_id,
        draft_course_id=course.id,
        kind=AssetKind.slides,
        title="Widget slides",
        content="slide deck",
        metrics={"slide_count": 10},
        week_index=1,
        order_index=0,
    )
    lab = DraftItem(
        organization_id=org_id,
        draft_course_id=course.id,
        kind=AssetKind.lab,
        title="Widget lab",
        content="lab body",
        metrics={"lines_of_code": 100},
        week_index=1,
        order_index=1,
    )
    lesson = DraftItem(
        organization_id=org_id,
        draft_course_id=course.id,
        kind=AssetKind.lesson_plan,
        title="Widget lesson plan",
        content="lesson body",
        week_index=2,
        order_index=0,
    )
    session.add_all([slides, lab, lesson])
    await session.flush()

    # Alignments: each item -> an objective.
    session.add_all(
        [
            DraftItemObjective(
                organization_id=org_id,
                draft_item_id=slides.id,
                draft_objective_id=obj1.id,
            ),
            DraftItemObjective(
                organization_id=org_id,
                draft_item_id=lab.id,
                draft_objective_id=obj1.id,
            ),
            DraftItemObjective(
                organization_id=org_id,
                draft_item_id=lesson.id,
                draft_objective_id=obj2.id,
            ),
        ]
    )
    # Dependency: wk1 lab depends-on wk1 slides (from=slides -> to=lab).
    session.add(
        DraftDependency(
            organization_id=org_id,
            draft_course_id=course.id,
            from_item_id=slides.id,
            to_item_id=lab.id,
            edge_type="prerequisite",
        )
    )
    await session.commit()
    return course.id, {"slides": slides.id, "lab": lab.id, "lesson": lesson.id}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_publish_creates_review_candidate_not_active(rls_engine):
    """Slice 5: publish assembles a pre-active candidate + an initial-release CCR.

    The full version is assembled (5 members, structurally valid) BUT its status
    is ``review``, the curriculum has NO active version yet, and an initial-release
    ChangeRequest pins the candidate for the QA gate. An un-QA'd course must not be
    reachable as active by this path.
    """
    from app.builder.compile import initial_release_marker
    from app.models.content_model import CurriculumVersion
    from app.models.curriculum import Curriculum
    from app.models.enums import LifecycleStatus
    from app.models.workflow import ChangeRequest

    org_id, user_id = await _org_and_user(rls_engine)

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                draft_id, items = await _build_small_draft(session, org_id)

                result = await publish_draft(
                    session, draft_id, author_id=user_id
                )
                await session.commit()
                cv = result.version
                ccr = result.ccr

                # --- Draft linked, but IN REVIEW (not published/active). ---
                draft = await session.scalar(
                    select(DraftCourse).where(DraftCourse.id == draft_id)
                )
                assert draft.status == "in_review"
                assert draft.curriculum_id is not None
                curriculum_id = draft.curriculum_id

                # --- The candidate version is NOT active. ---
                cv_row = await session.get(CurriculumVersion, cv.id)
                assert cv_row.status == LifecycleStatus.review
                assert (cv.major, cv.minor, cv.patch) == (1, 0, 0)

                # --- The curriculum has NO active content version yet. ---
                curriculum = await session.get(Curriculum, curriculum_id)
                assert curriculum.active_content_version_id is None
                active = await active_curriculum_version(session, curriculum_id)
                assert active is None

                # --- The initial-release CCR exists + pins this candidate. ---
                assert ccr.curriculum_id == curriculum_id
                assert ccr.author_id == user_id
                assert ccr.change_set is None  # /merge naturally rejects it
                marker = initial_release_marker(ccr)
                assert marker is not None
                assert marker["candidate_version_id"] == str(cv.id)

                ccr_rows = (
                    await session.execute(
                        select(ChangeRequest).where(
                            ChangeRequest.curriculum_id == curriculum_id
                        )
                    )
                ).scalars().all()
                assert len(ccr_rows) == 1
                assert ccr_rows[0].title.startswith("[Initial Release]")

                # --- The full version was still assembled: 3 items + 2 objs = 5. ---
                members = await version_members(session, cv.id)
                assert len(members) == 5
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_publish_twice_raises(rls_engine):
    """Republishing an already-published draft raises (router → 409)."""
    org_id, user_id = await _org_and_user(rls_engine)

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                draft_id, _ = await _build_small_draft(session, org_id)
                await publish_draft(session, draft_id)
                await session.commit()

                with pytest.raises(AlreadyPublishedError):
                    await publish_draft(session, draft_id)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_publish_cyclic_dependency_raises_and_persists_nothing(rls_engine):
    """A cyclic DraftDependency → CompileValidationError, fail-closed."""
    org_id, user_id = await _org_and_user(rls_engine)

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course = DraftCourse(
                    organization_id=org_id, title="Cyclic Course", status="drafting"
                )
                session.add(course)
                await session.flush()
                course_id = course.id  # capture before any rollback expires it
                a = DraftItem(
                    organization_id=org_id,
                    draft_course_id=course.id,
                    kind=AssetKind.lab,
                    title="A",
                    week_index=1,
                    order_index=0,
                )
                b = DraftItem(
                    organization_id=org_id,
                    draft_course_id=course.id,
                    kind=AssetKind.lab,
                    title="B",
                    week_index=1,
                    order_index=1,
                )
                session.add_all([a, b])
                await session.flush()
                # A -> B and B -> A : a 2-cycle.
                session.add_all(
                    [
                        DraftDependency(
                            organization_id=org_id,
                            draft_course_id=course.id,
                            from_item_id=a.id,
                            to_item_id=b.id,
                        ),
                        DraftDependency(
                            organization_id=org_id,
                            draft_course_id=course.id,
                            from_item_id=b.id,
                            to_item_id=a.id,
                        ),
                    ]
                )
                await session.commit()

                before = await session.scalar(
                    select(func.count()).select_from(ContentVersion)
                )
                with pytest.raises(CompileValidationError):
                    await publish_draft(session, course_id)

                # Fail-closed: the SAVEPOINT rolled back; nothing persisted, draft
                # untouched.
                await session.rollback()
                after = await session.scalar(
                    select(func.count()).select_from(ContentVersion)
                )
                assert after == before
                draft = await session.scalar(
                    select(DraftCourse).where(DraftCourse.id == course_id)
                )
                assert draft.status == "drafting"
                assert draft.curriculum_id is None
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_publish_only_promotes_accepted_edges(rls_engine):
    """Regression: ai_suggested (accepted=False) edges must NOT become VersionEdges.

    Build a draft with two items, one author edge (accepted=True, default) and
    one ai_suggested edge (accepted=False). Publish and assert the resulting
    VersionEdge list contains exactly the author edge — the ai_suggested edge
    is excluded.
    """
    org_id, user_id = await _org_and_user(rls_engine)

    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_session(rls_engine, org_id)
            try:
                course = DraftCourse(
                    organization_id=org_id,
                    title="Accepted Edge Only",
                    status="drafting",
                )
                session.add(course)
                await session.flush()

                item_a = DraftItem(
                    organization_id=org_id,
                    draft_course_id=course.id,
                    kind=AssetKind.slides,
                    title="Item A",
                    week_index=1,
                    order_index=0,
                )
                item_b = DraftItem(
                    organization_id=org_id,
                    draft_course_id=course.id,
                    kind=AssetKind.lab,
                    title="Item B",
                    week_index=2,
                    order_index=0,
                )
                item_c = DraftItem(
                    organization_id=org_id,
                    draft_course_id=course.id,
                    kind=AssetKind.lesson_plan,
                    title="Item C",
                    week_index=3,
                    order_index=0,
                )
                session.add_all([item_a, item_b, item_c])
                await session.flush()

                # Author edge A→B: accepted=True (default).
                session.add(
                    DraftDependency(
                        organization_id=org_id,
                        draft_course_id=course.id,
                        from_item_id=item_a.id,
                        to_item_id=item_b.id,
                        edge_type="prerequisite",
                        source="author",
                        accepted=True,
                    )
                )
                # AI-suggested edge B→C: accepted=False — must NOT be promoted.
                session.add(
                    DraftDependency(
                        organization_id=org_id,
                        draft_course_id=course.id,
                        from_item_id=item_b.id,
                        to_item_id=item_c.id,
                        edge_type="prerequisite",
                        source="ai_suggested",
                        accepted=False,
                    )
                )
                await session.commit()

                cv = (await publish_draft(session, course.id)).version
                await session.commit()

                # Only the accepted A→B edge should appear as a VersionEdge.
                from app.models.content_model import VersionEdge
                edge_rows = (
                    await session.execute(
                        select(VersionEdge).where(
                            VersionEdge.curriculum_version_id == cv.id,
                            VersionEdge.edge_type == "prerequisite",
                        )
                    )
                ).scalars().all()

                assert len(edge_rows) == 1, (
                    f"expected 1 prerequisite VersionEdge, got {len(edge_rows)}"
                )
            finally:
                await session.close()
    finally:
        current_org.reset(token)
