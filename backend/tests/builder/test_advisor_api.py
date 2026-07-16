"""Phase 2 Task 1 — AI categorizer + andragogy advisor API.

Drives the real ``app.builder.router_advisor`` handlers against a freshly-
created, RLS-enabled schema. Tests call the handlers DIRECTLY (no HTTP),
injecting fakes — ZERO real Anthropic calls in CI.

The test harness (``rls_engine``, ``_two_orgs_and_user``, ``_open_org_session``,
``current_org``/``use_org``) is an exact copy of the one in ``test_course_api.py``
(the canonical builder-test pattern, P-006).

What is asserted:
  * categorize-ai  — returns the expected result, does NOT mutate the item.
  * advise         — persists notes; list returns them; PATCH flips status.
  * 503            — ``get_categorizer`` / ``get_advisor`` raise 503 without key.
  * 404            — unknown item / course raises 404.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.ai.builder_advisor import AdviceItem, AdviceReport, CategorizeResult
from app.builder.models import DraftItem
from sqlalchemy import select
from app.builder.router_advisor import (
    advise_course,
    categorize_item_ai,
    get_advisor,
    get_categorizer,
    list_advisor_notes,
    update_advisor_note_status,
)
from app.builder.router_course import create_course, create_item, create_objective
from app.builder.schemas import (
    AdvisorNoteStatusUpdate,
    AdviseRequest,
    CourseCreate,
    ItemCreate,
    ObjectiveCreate,
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
# Test harness (mirrors test_course_api.py exactly)
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
            email="advisor-author@example.com",
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
# Module-level fakes — no real Anthropic calls, ever
# ---------------------------------------------------------------------------


class FakeCategorizer:
    """Fake Categorizer: returns canned CategorizeResult, ignores all inputs."""

    async def categorize(
        self, *, title: str, content: str | None, objectives: list[str]
    ) -> CategorizeResult:
        return CategorizeResult(
            kind="lab",
            served_objective_hint=objectives[0] if objectives else "n/a",
            estimated_minutes=90,
            complexity=1.2,
            rationale="hands-on",
        )


class FakeAdvisor:
    """Fake AndragogyAdvisor: returns two canned AdviceItems, ignores inputs."""

    async def advise(
        self,
        *,
        course_context: str,
        learner_profile: dict,
        focus: str | None = None,
    ) -> AdviceReport:
        return AdviceReport(
            notes=[
                AdviceItem(kind="question", text="Q1", target_kind="course"),
                AdviceItem(
                    kind="suggestion",
                    text="S1",
                    target_kind="week",
                    target_ref="2",
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_categorize_ai_returns_result_without_mutating_item(rls_engine):
    """categorize-ai returns correct values AND leaves the item row unchanged."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Categorize Test Course"),
                    current=current,
                    db=session,
                )
                item = await create_item(
                    course.id,
                    # Use "slides" — distinct from the fake's "lab" output so
                    # the no-mutation assertion is meaningful.
                    ItemCreate(title="Hands-on Lab Exercise", kind=AssetKind.slides),
                    current=current,
                    db=session,
                )

                result = await categorize_item_ai(
                    item.id,
                    current=current,
                    db=session,
                    analyzer=FakeCategorizer(),
                )

                # Returned values must match the fake's canned output.
                assert result.kind == "lab"
                assert result.estimated_minutes == 90
                assert result.complexity == pytest.approx(1.2)
                assert result.rationale == "hands-on"

                # The item row must NOT be mutated (stateless preview).
                refreshed = (
                    await session.execute(
                        select(DraftItem).where(DraftItem.id == item.id)
                    )
                ).scalar_one()
                assert refreshed.kind == AssetKind.slides  # unchanged (slides, not lab)
                assert refreshed.estimated_minutes is None  # unchanged

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_advise_persists_and_lists_notes(rls_engine):
    """advise returns + persists notes; list returns them; PATCH flips status."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Andragogy Advise Test"),
                    current=current,
                    db=session,
                )
                course_id = course.id

                # Add an objective and an item so the context builder has data.
                await create_objective(
                    course_id,
                    ObjectiveCreate(text="Apply async patterns in Python", week_index=1),
                    current=current,
                    db=session,
                )
                await create_item(
                    course_id,
                    ItemCreate(title="Async Lecture", kind=AssetKind.slides, week_index=1),
                    current=current,
                    db=session,
                )

                # Call advise — should persist 2 notes.
                notes = await advise_course(
                    course_id,
                    AdviseRequest(),
                    current=current,
                    db=session,
                    analyzer=FakeAdvisor(),
                )
                assert len(notes) == 2
                assert {n.kind for n in notes} == {"question", "suggestion"}
                assert all(n.status == "open" for n in notes)

                # GET-notes must return the same 2 persisted rows.
                listed = await list_advisor_notes(
                    course_id, current=current, db=session
                )
                assert len(listed) == 2

                # PATCH one note to "dismissed" — status must flip.
                note_to_patch = notes[0]
                patched = await update_advisor_note_status(
                    note_to_patch.id,
                    AdvisorNoteStatusUpdate(status="dismissed"),
                    current=current,
                    db=session,
                )
                assert patched.status == "dismissed"
                assert patched.id == note_to_patch.id

            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_get_categorizer_503_without_key():
    """get_categorizer() raises 503 when ANTHROPIC_API_KEY is empty."""
    with mock.patch.object(settings, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            get_categorizer()
        assert exc.value.status_code == 503


async def test_get_advisor_503_without_key():
    """get_advisor() raises 503 when ANTHROPIC_API_KEY is empty."""
    with mock.patch.object(settings, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            get_advisor()
        assert exc.value.status_code == 503


async def test_categorize_unknown_item_404(rls_engine):
    """categorize-ai on an unknown item_id raises 404."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await categorize_item_ai(
                        uuid.uuid4(),
                        current=current,
                        db=session,
                        analyzer=FakeCategorizer(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_advise_unknown_course_404(rls_engine):
    """advise on an unknown course_id raises 404."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await advise_course(
                        uuid.uuid4(),
                        AdviseRequest(),
                        current=current,
                        db=session,
                        analyzer=FakeAdvisor(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_patch_unknown_note_404(rls_engine):
    """PATCH /advisor-notes/{note_id} with a non-existent note_id raises 404."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await update_advisor_note_status(
                        uuid.uuid4(),
                        AdvisorNoteStatusUpdate(status="dismissed"),
                        current=current,
                        db=session,
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_advisor_notes_tenant_isolation(rls_engine):
    """Advisor notes for org A's course are not visible from org B.

    Creates a course + advisor notes under org A, then opens a session under
    org B and GETs the notes for that course id — asserts 404 because
    ``_get_course`` cannot see org A's course through org B's RLS filter.
    """
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    # Create a course + persist advisor notes under org A.
    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Org A Private Course"),
                    current=current,
                    db=session,
                )
                course_id = course.id
                await advise_course(
                    course_id,
                    AdviseRequest(),
                    current=current,
                    db=session,
                    analyzer=FakeAdvisor(),
                )
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    # Open a session under org B — the course is invisible, so list_advisor_notes
    # must raise 404, not leak the notes.
    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(HTTPException) as exc:
                    await list_advisor_notes(
                        course_id, current=current, db=session
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
