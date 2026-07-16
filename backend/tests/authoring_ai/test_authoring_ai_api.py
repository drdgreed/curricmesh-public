"""Authoring Platform slice 3 — per-aspect generator API (author-time router).

Drives the real ``app.routers.authoring_ai`` handlers against a freshly-created,
RLS-enabled schema. Handlers are called DIRECTLY (no HTTP), injecting a FAKE
CourseAuthorAI — ZERO real Anthropic calls in CI.

The harness (``rls_engine``, ``_two_orgs_and_user``, ``_open_org_session``,
``current_org`` / ``use_org``) mirrors tests/builder/test_advisor_api.py.

Asserted:
  * generate-objectives — returns the draft; topic defaults to the course title;
    the generator receives the course topic; cross-org course -> 404.
  * generate-content    — returns the draft; the generator receives the item kind
    + linked objective grounding; cross-org item -> 404.
  * generate-assessment — returns the draft; the generator receives the objective;
    cross-org objective -> 404.
  * 403 — a non-author role is rejected by the role gate.
  * 503 — get_author_ai raises without an API key.
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
from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedItemContent,
    GeneratedObjective,
    GeneratedObjectives,
)
from app.builder.router_course import (
    align_item,
    create_course,
    create_item,
    create_objective,
)
from app.builder.schemas import (
    AlignmentCreate,
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
from app.routers.authoring_ai import (
    _AUTHOR_ROLES,
    GenerateObjectivesRequest,
    generate_assessment_endpoint,
    generate_content_endpoint,
    generate_objectives_endpoint,
    get_author_ai,
)
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# ---------------------------------------------------------------------------
# Test harness (mirrors test_advisor_api.py exactly)
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


async def _two_orgs_and_user(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.flush()
        user = User(
            email="authoring-ai@example.com",
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
# Fake CourseAuthorAI — captures grounding, returns canned drafts
# ---------------------------------------------------------------------------


class FakeAuthorAI:
    """Fake CourseAuthorAI: records the grounding it was handed, returns canned drafts."""

    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}

    async def generate_objectives(
        self, *, topic: str, learner_profile: dict, count: int = 5,
        language: str = "en",
    ) -> GeneratedObjectives:
        self.calls["objectives"] = {
            "topic": topic,
            "learner_profile": learner_profile,
            "count": count,
            "language": language,
        }
        return GeneratedObjectives(
            objectives=[
                GeneratedObjective(
                    text="Explain the topic",
                    bloom_level="understand",
                    key_skills=["x"],
                )
            ]
        )

    async def generate_item_content(
        self, *, objective: str, kind: str, course_context: str,
        language: str = "en",
    ) -> GeneratedItemContent:
        self.calls["content"] = {
            "objective": objective,
            "kind": kind,
            "course_context": course_context,
            "language": language,
        }
        return GeneratedItemContent(
            kind=kind,
            content_markdown="# Draft",
            summary="A draft body.",
            caveats=[],
        )

    async def generate_assessment(
        self, *, objective: str, course_context: str, language: str = "en",
    ) -> GeneratedAssessment:
        self.calls["assessment"] = {
            "objective": objective,
            "course_context": course_context,
            "language": language,
        }
        return GeneratedAssessment(
            content_markdown="## Quiz",
            rubric="rubric",
            caveats=[],
        )


# ---------------------------------------------------------------------------
# generate-objectives
# ---------------------------------------------------------------------------


async def test_generate_objectives_returns_draft_and_defaults_topic(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="AI Engineering 101", description="Build agents"),
                    current=current,
                    db=session,
                )
                fake = FakeAuthorAI()
                # No topic in body → defaults to the course title/description.
                result = await generate_objectives_endpoint(
                    course.id,
                    GenerateObjectivesRequest(),
                    current=current,
                    db=session,
                    author_ai=fake,
                )
                assert result.objectives[0].bloom_level == "understand"
                # The generator must have received the course title as topic grounding.
                assert "AI Engineering 101" in fake.calls["objectives"]["topic"]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_objectives_uses_explicit_topic_and_count(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Course"), current=current, db=session
                )
                fake = FakeAuthorAI()
                await generate_objectives_endpoint(
                    course.id,
                    GenerateObjectivesRequest(topic="Vector databases", count=3),
                    current=current,
                    db=session,
                    author_ai=fake,
                )
                assert fake.calls["objectives"]["topic"] == "Vector databases"
                assert fake.calls["objectives"]["count"] == 3
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_objectives_unknown_course_404(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_objectives_endpoint(
                        uuid.uuid4(),
                        GenerateObjectivesRequest(),
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_objectives_cross_org_404(rls_engine):
    """A course created under org A is invisible (404) to a session pinned to org B."""
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Org A Course"), current=current, db=session
                )
                course_id = course.id
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_objectives_endpoint(
                        course_id,
                        GenerateObjectivesRequest(),
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# generate-content
# ---------------------------------------------------------------------------


async def test_generate_content_returns_draft_with_kind_and_objective_grounding(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="AI Engineering 101"),
                    current=current,
                    db=session,
                )
                objective = await create_objective(
                    course.id,
                    ObjectiveCreate(text="Build a tool-using agent", week_index=1),
                    current=current,
                    db=session,
                )
                item = await create_item(
                    course.id,
                    ItemCreate(title="Agent Lab", kind=AssetKind.lab, week_index=1),
                    current=current,
                    db=session,
                )
                # Link the objective to the item so the generator is grounded.
                await align_item(
                    item.id,
                    AlignmentCreate(objective_id=objective.id),
                    current=current,
                    db=session,
                )

                fake = FakeAuthorAI()
                result = await generate_content_endpoint(
                    item.id,
                    current=current,
                    db=session,
                    author_ai=fake,
                )
                assert result.kind == "lab"
                # The generator received the item's kind + the linked objective + course.
                assert fake.calls["content"]["kind"] == "lab"
                assert "Build a tool-using agent" in fake.calls["content"]["objective"]
                assert "AI Engineering 101" in fake.calls["content"]["course_context"]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_content_without_linked_objective_still_works(rls_engine):
    """An item with no linked objective still generates (grounds on the item title)."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Course"), current=current, db=session
                )
                item = await create_item(
                    course.id,
                    ItemCreate(title="Intro Lecture", kind=AssetKind.slides),
                    current=current,
                    db=session,
                )
                fake = FakeAuthorAI()
                result = await generate_content_endpoint(
                    item.id, current=current, db=session, author_ai=fake
                )
                assert result.kind == "slides"
                # Title-derived grounding when no objective is linked.
                assert "Intro Lecture" in fake.calls["content"]["objective"]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_content_unknown_item_404(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_content_endpoint(
                        uuid.uuid4(),
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_content_cross_org_404(rls_engine):
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Course"), current=current, db=session
                )
                item = await create_item(
                    course.id,
                    ItemCreate(title="Lab", kind=AssetKind.lab),
                    current=current,
                    db=session,
                )
                item_id = item.id
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_content_endpoint(
                        item_id,
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# generate-assessment
# ---------------------------------------------------------------------------


async def test_generate_assessment_returns_draft_with_objective_grounding(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="AI Engineering 101"),
                    current=current,
                    db=session,
                )
                objective = await create_objective(
                    course.id,
                    ObjectiveCreate(text="Diagnose a failing agent loop"),
                    current=current,
                    db=session,
                )
                fake = FakeAuthorAI()
                result = await generate_assessment_endpoint(
                    objective.id,
                    current=current,
                    db=session,
                    author_ai=fake,
                )
                assert result.content_markdown == "## Quiz"
                assert "Diagnose a failing agent loop" in fake.calls["assessment"]["objective"]
                assert "AI Engineering 101" in fake.calls["assessment"]["course_context"]
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_assessment_unknown_objective_404(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_assessment_endpoint(
                        uuid.uuid4(),
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_assessment_cross_org_404(rls_engine):
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Course"), current=current, db=session
                )
                objective = await create_objective(
                    course.id,
                    ObjectiveCreate(text="An objective"),
                    current=current,
                    db=session,
                )
                objective_id = objective.id
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_assessment_endpoint(
                        objective_id,
                        current=current,
                        db=session,
                        author_ai=FakeAuthorAI(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# Role gate + missing-key
# ---------------------------------------------------------------------------


def test_author_role_gate_rejects_non_author():
    """The role gate rejects a role outside the author tier with 403."""
    with pytest.raises(HTTPException) as exc:
        _AUTHOR_ROLES(current={"sub": "u", "role": "student"})
    assert exc.value.status_code == 403


def test_author_role_gate_allows_author():
    current = {"sub": "u", "role": "instructor"}
    assert _AUTHOR_ROLES(current=current) is current


def test_get_author_ai_503_without_key():
    with mock.patch.object(settings, "ANTHROPIC_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            get_author_ai()
        assert exc.value.status_code == 503
