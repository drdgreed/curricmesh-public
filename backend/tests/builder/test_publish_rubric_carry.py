"""Rubric carry-through on publish.

Publishing a draft whose assessment item has ``ai_notes["rubric"]`` set must
freeze the rubric into the item's immutable ``ContentVersion.metadata_["rubric"]``
so the assessment-feedback tutor (B5) can read it from the released version.

Three cases:
  1. Assessment item with a rubric → ``metadata_["rubric"]`` present and equal.
  2. Non-assessment item (no rubric in ai_notes) → no ``"rubric"`` key in metadata.
  3. Assessment item with ai_notes present but rubric key missing → no key written.

Fixture style mirrors ``tests/builder/test_publish_media_carry_through.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.builder.compile import publish_draft
from app.builder.models import DraftCourse, DraftItem
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.content_model import ContentVersion
from app.models.enums import AssetKind
from app.models.org import Organization
from app.tenant import use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

_RUBRIC = (
    "## Scoring rubric\n\n"
    "| Criterion | Full credit | Partial | No credit |\n"
    "|---|---|---|---|\n"
    "| Correctness | All assertions pass | 1-2 failures | 3+ failures |"
)


@pytest.fixture
async def rls_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _org(engine) -> uuid.UUID:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org = Organization(name="Rubric Test Org")
        s.add(org)
        await s.commit()
        return org.id


async def _open_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_publish_carries_rubric_from_ai_notes(rls_engine):
    """Assessment item with ai_notes["rubric"] → ContentVersion.metadata_["rubric"]."""
    org_id = await _org(rls_engine)

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            course = DraftCourse(title="Rubric course", status="drafting")
            session.add(course)
            await session.flush()

            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.assessment,
                title="Week 1 assessment",
                content="Q1: Describe the agentic loop.",
                week_index=1,
                order_index=0,
                ai_notes={
                    "rubric": _RUBRIC,
                    "caveats": ["Human must verify alignment with LOs."],
                },
            )
            session.add(item)
            await session.flush()

            await publish_draft(session, course.id)
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cvs = (await session.execute(select(ContentVersion))).scalars().all()
            # Only one item was published, so exactly one ContentVersion.
            assert len(cvs) == 1
            cv = cvs[0]
            assert cv.metadata_ is not None
            assert "rubric" in cv.metadata_, (
                "rubric must be carried from ai_notes into ContentVersion.metadata_"
            )
            assert cv.metadata_["rubric"] == _RUBRIC
        finally:
            await session.close()


async def test_publish_no_rubric_on_non_assessment_item(rls_engine):
    """A lesson item with no rubric in ai_notes must NOT gain a rubric key."""
    org_id = await _org(rls_engine)

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            course = DraftCourse(title="Lesson-only course", status="drafting")
            session.add(course)
            await session.flush()

            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.lesson_plan,
                title="Week 1 lesson",
                content="Lesson content here.",
                week_index=1,
                order_index=0,
                ai_notes={"summary": "Intro to agents.", "caveats": []},
            )
            session.add(item)
            await session.flush()

            await publish_draft(session, course.id)
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cvs = (await session.execute(select(ContentVersion))).scalars().all()
            assert len(cvs) == 1
            cv = cvs[0]
            assert cv.metadata_ is not None
            assert "rubric" not in cv.metadata_, (
                "non-assessment item must not have rubric in ContentVersion.metadata_"
            )
        finally:
            await session.close()


async def test_publish_no_rubric_key_when_ai_notes_lacks_rubric(rls_engine):
    """Assessment item whose ai_notes dict has no 'rubric' key → no rubric written."""
    org_id = await _org(rls_engine)

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            course = DraftCourse(title="Partial notes course", status="drafting")
            session.add(course)
            await session.flush()

            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.assessment,
                title="Draft assessment",
                content="TBD",
                week_index=1,
                order_index=0,
                ai_notes={"caveats": ["Rubric not yet drafted."]},
            )
            session.add(item)
            await session.flush()

            await publish_draft(session, course.id)
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cvs = (await session.execute(select(ContentVersion))).scalars().all()
            assert len(cvs) == 1
            cv = cvs[0]
            assert "rubric" not in (cv.metadata_ or {}), (
                "rubric key must not appear when ai_notes has no rubric"
            )
        finally:
            await session.close()


async def test_publish_no_rubric_when_ai_notes_is_null(rls_engine):
    """Item with ai_notes=None → metadata unchanged, no rubric key."""
    org_id = await _org(rls_engine)

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            course = DraftCourse(title="Null notes course", status="drafting")
            session.add(course)
            await session.flush()

            item = DraftItem(
                draft_course_id=course.id,
                kind=AssetKind.assessment,
                title="Bare assessment",
                content="Answer this question.",
                week_index=1,
                order_index=0,
                ai_notes=None,
            )
            session.add(item)
            await session.flush()

            await publish_draft(session, course.id)
            await session.commit()
        finally:
            await session.close()

    with use_org(org_id):
        session = await _open_session(rls_engine, org_id)
        try:
            cvs = (await session.execute(select(ContentVersion))).scalars().all()
            assert len(cvs) == 1
            assert "rubric" not in (cvs[0].metadata_ or {})
        finally:
            await session.close()
