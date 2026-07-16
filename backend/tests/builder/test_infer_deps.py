"""Phase 2 Task 2 — AI prerequisite inference (infer-deps endpoint).

Drives the real ``app.builder.router_advisor.infer_deps`` handler against a
freshly-created, RLS-enabled schema.  Tests call the handler DIRECTLY (no
HTTP), injecting a ``FakePrereqInferer`` — ZERO real Anthropic calls in CI.

Harness mirrors ``test_advisor_api.py`` exactly.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest import mock

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.ai.builder_advisor import (
    MissingDependency,
    PrereqReport,
    PrereqSuggestion,
)
from app.builder.models import DraftAdvisorNote, DraftDependency
from app.builder.router_advisor import (
    get_prereq_inferer,
    infer_deps,
)
from app.builder.router_course import create_course, create_dependency, create_item
from app.builder.schemas import CourseCreate, DependencyCreate, ItemCreate
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# ---------------------------------------------------------------------------
# Test harness (mirrors test_advisor_api.py exactly)
# ---------------------------------------------------------------------------


@pytest.fixture
async def rls_engine():
    """A dedicated engine on a fresh, RLS-enabled schema (no seed needed)."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _two_orgs_and_user(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed two orgs + one user in org A. Returns (org_a, org_b, user_id)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.flush()
        user = User(
            email="infer-deps-author@example.com",
            role="instructor",
            organization_id=org_a.id,
        )
        s.add(user)
        await s.commit()
        return org_a.id, org_b.id, user.id


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Fake inferer — no real Anthropic calls, ever
# ---------------------------------------------------------------------------


class FakePrereqInferer:
    """Fake PrereqInferer: returns a canned report, ignores all inputs."""

    def __init__(self, report: PrereqReport) -> None:
        self._report = report

    async def infer(self, *, items: list[dict]) -> PrereqReport:
        return self._report


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_infer_creates_suggested_dep_and_missing_note(rls_engine):
    """Happy path: one suggested edge and one missing flag are persisted."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Prereq Test Course"),
                    current=current,
                    db=session,
                )
                item_a = await create_item(
                    course.id,
                    ItemCreate(title="Item A", kind=AssetKind.slides, week_index=1),
                    current=current,
                    db=session,
                )
                item_b = await create_item(
                    course.id,
                    ItemCreate(title="Item B", kind=AssetKind.lab, week_index=2),
                    current=current,
                    db=session,
                )

                report = PrereqReport(
                    suggested=[
                        PrereqSuggestion(
                            from_title="Item A",
                            to_title="Item B",
                            reason="A introduces concepts needed by B",
                        )
                    ],
                    missing=[
                        MissingDependency(
                            item_title="Item A",
                            needs="Basic Python",
                            reason="Item A uses Python syntax not taught earlier",
                        )
                    ],
                )

                result = await infer_deps(
                    course.id,
                    current=current,
                    db=session,
                    inferer=FakePrereqInferer(report),
                )

                assert result.suggested_created == 1
                assert result.missing_flagged == 1

                # Assert exactly one DraftDependency row with correct attributes.
                dep_rows = (
                    await session.execute(
                        select(DraftDependency).where(
                            DraftDependency.draft_course_id == course.id
                        )
                    )
                ).scalars().all()
                assert len(dep_rows) == 1
                dep = dep_rows[0]
                assert dep.from_item_id == item_a.id
                assert dep.to_item_id == item_b.id
                assert dep.source == "ai_suggested"
                assert dep.accepted is False
                assert dep.edge_type == "prerequisite"

                # Assert exactly one DraftAdvisorNote warning row.
                note_rows = (
                    await session.execute(
                        select(DraftAdvisorNote).where(
                            DraftAdvisorNote.draft_course_id == course.id
                        )
                    )
                ).scalars().all()
                assert len(note_rows) == 1
                note = note_rows[0]
                assert note.kind == "warning"
                assert note.status == "open"
                assert note.target_kind == "item"
                assert note.target_ref == str(item_a.id)

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_infer_skips_cycle_creating_suggestion(rls_engine):
    """Suggestion that would create a cycle (B→A when A→B accepted) is skipped."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Cycle Skip Course"),
                    current=current,
                    db=session,
                )
                item_a = await create_item(
                    course.id,
                    ItemCreate(title="Item A", kind=AssetKind.slides),
                    current=current,
                    db=session,
                )
                item_b = await create_item(
                    course.id,
                    ItemCreate(title="Item B", kind=AssetKind.lab),
                    current=current,
                    db=session,
                )

                # Pre-create an ACCEPTED edge A→B.
                await create_dependency(
                    course.id,
                    DependencyCreate(from_item_id=item_a.id, to_item_id=item_b.id),
                    current=current,
                    db=session,
                )

                # Report suggests B→A — would create a cycle.
                report = PrereqReport(
                    suggested=[
                        PrereqSuggestion(
                            from_title="Item B",
                            to_title="Item A",
                            reason="This would cycle",
                        )
                    ],
                    missing=[],
                )

                result = await infer_deps(
                    course.id,
                    current=current,
                    db=session,
                    inferer=FakePrereqInferer(report),
                )

                assert result.suggested_created == 0
                assert result.missing_flagged == 0

                # Only the original A→B row exists; no B→A row was created.
                count = (
                    await session.execute(
                        select(func.count()).select_from(DraftDependency).where(
                            DraftDependency.draft_course_id == course.id
                        )
                    )
                ).scalar_one()
                assert count == 1

                bad_row = (
                    await session.execute(
                        select(DraftDependency).where(
                            DraftDependency.draft_course_id == course.id,
                            DraftDependency.from_item_id == item_b.id,
                            DraftDependency.to_item_id == item_a.id,
                        )
                    )
                ).scalar_one_or_none()
                assert bad_row is None

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_infer_skips_unresolvable_and_duplicate(rls_engine):
    """Unresolvable titles and duplicate edges are both silently skipped."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Skip Cases Course"),
                    current=current,
                    db=session,
                )
                item_a = await create_item(
                    course.id,
                    ItemCreate(title="Item A", kind=AssetKind.slides),
                    current=current,
                    db=session,
                )
                item_b = await create_item(
                    course.id,
                    ItemCreate(title="Item B", kind=AssetKind.lab),
                    current=current,
                    db=session,
                )

                # Pre-create an existing edge A→B (any source/accepted).
                await create_dependency(
                    course.id,
                    DependencyCreate(from_item_id=item_a.id, to_item_id=item_b.id),
                    current=current,
                    db=session,
                )

                # Report 1: edge referencing a title not in the course.
                # Report 2: duplicate of the existing A→B edge.
                report = PrereqReport(
                    suggested=[
                        PrereqSuggestion(
                            from_title="Ghost Item",  # not in the course
                            to_title="Item B",
                            reason="unresolvable from_title",
                        ),
                        PrereqSuggestion(
                            from_title="Item A",
                            to_title="Item B",  # already exists
                            reason="duplicate edge",
                        ),
                    ],
                    missing=[],
                )

                result = await infer_deps(
                    course.id,
                    current=current,
                    db=session,
                    inferer=FakePrereqInferer(report),
                )

                assert result.suggested_created == 0
                assert result.missing_flagged == 0

                # Still exactly one dep row (the pre-created A→B).
                count = (
                    await session.execute(
                        select(func.count()).select_from(DraftDependency).where(
                            DraftDependency.draft_course_id == course.id
                        )
                    )
                ).scalar_one()
                assert count == 1

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_infer_unknown_course_404(rls_engine):
    """infer_deps on an unknown course_id raises 404."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await infer_deps(
                        uuid.uuid4(),
                        current=current,
                        db=session,
                        inferer=FakePrereqInferer(
                            PrereqReport(suggested=[], missing=[])
                        ),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_infer_skips_intrabatch_cycle(rls_engine):
    """Intra-batch contradictory suggestions: A→B inserted, then B→A skipped.

    Course has items A and B with NO pre-existing edges.  The inferer report
    suggests BOTH A→B and B→A in a single batch.  Exactly ONE DraftDependency
    row should be created (suggested_created == 1); the second suggestion is
    blocked by the in-memory cycle guard.
    """
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Intrabatch Cycle Course"),
                    current=current,
                    db=session,
                )
                item_a = await create_item(
                    course.id,
                    ItemCreate(title="Alpha", kind=AssetKind.slides, week_index=1),
                    current=current,
                    db=session,
                )
                item_b = await create_item(
                    course.id,
                    ItemCreate(title="Beta", kind=AssetKind.lab, week_index=2),
                    current=current,
                    db=session,
                )

                # Report proposes both directions — a self-contradictory batch.
                report = PrereqReport(
                    suggested=[
                        PrereqSuggestion(
                            from_title="Alpha",
                            to_title="Beta",
                            reason="Alpha comes before Beta",
                        ),
                        PrereqSuggestion(
                            from_title="Beta",
                            to_title="Alpha",
                            reason="contradicts the first suggestion",
                        ),
                    ],
                    missing=[],
                )

                result = await infer_deps(
                    course.id,
                    current=current,
                    db=session,
                    inferer=FakePrereqInferer(report),
                )

                # Exactly one edge should be created; the reverse is cycle-blocked.
                assert result.suggested_created == 1

                dep_rows = (
                    await session.execute(
                        select(DraftDependency).where(
                            DraftDependency.draft_course_id == course.id
                        )
                    )
                ).scalars().all()
                assert len(dep_rows) == 1

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_get_prereq_inferer_503_without_key():
    """get_prereq_inferer() raises 503 when ANTHROPIC_API_KEY is empty."""
    with mock.patch.object(settings, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            get_prereq_inferer()
        assert exc.value.status_code == 503
