"""Release→ingest hook tests — ingest fires on BOTH release paths.

Proves the convergence wiring: once a ``CurriculumVersion`` becomes active, the
release endpoints schedule ``run_ingest`` on a background task, so the retrieval
index (``ContentChunk`` rows) is built for the newly-active version.

Two release paths make a CurriculumVersion active — both are covered:
  * INITIAL RELEASE — ``POST /ccrs/{id}/release`` on a first-time authored course
    (``activate_initial_release`` flips the candidate CurriculumVersion active).
    Driven through the REAL HTTP stack; ASGITransport awaits background tasks
    before the response resolves, so the scheduled ingest has already run.
  * CCR MERGE — ``POST /ccrs/{id}/merge`` (fork() forks + activates a new
    CurriculumVersion). Driven through the real ``merge`` handler on a
    seeded+backfilled engine, executing the scheduled background task explicitly.

The legacy ``release_ccr`` path (non-initial ``/release``) activates a legacy
``Version`` model — NOT a ``CurriculumVersion`` and it has no ``VersionMember``
rows — so it is out of scope for retrieval ingest (nothing to index) and is not
hooked.

Injection keeps CI offline: ``get_ingest_session_scope`` is overridden to yield
the test session (no real app-engine connection), and the default embedder is
the ``FakeEmbedder`` (no real embedding API).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.auth.jwt import create_access_token
from app.builder.models import (
    DraftCourse,
    DraftItem,
    DraftItemObjective,
    DraftObjective,
)
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import record_approval, record_qa, submit_ccr
from app.core.workflow.rules import QA_DIMENSIONS
from app.database import Base, get_db
from app.db.rls import apply_rls
from app.main import app
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.retrieval import ContentChunk
from app.models.user import User
from app.models.workflow import ChangeRequest
from app.routers.approvals import get_ingest_session_scope, merge
from app.schemas.release import NewAssetIn, ReleaseChangeSet
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed
from tests.conftest import DEFAULT_ORG_ID

_FULL_SCORES = {dim: 5 for dim in QA_DIMENSIONS}
_ENUM_TYPES = ("lifecyclestatus", "assetkind")
SEEDED_SLUG = "agentic-ai"
_QA_PASS = {
    "content_accuracy": 5,
    "alignment": 5,
    "prerequisites": 5,
    "consistency": 5,
    "instructor_support": 5,
    "student_experience": 5,
}


async def _chunk_count(session: AsyncSession, version_id) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ContentChunk)
            .where(ContentChunk.curriculum_version_id == version_id)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# INITIAL-RELEASE path (POST /ccrs/{id}/release → activate_initial_release)
# ---------------------------------------------------------------------------


def _auth(role: str, sub: str) -> dict:
    tok = create_access_token(sub=sub, role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {tok}"}


async def _seed_user(session: AsyncSession, role: str) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@test.local", role=role, password_hash="x"
    )
    session.add(user)
    await session.commit()
    return user


async def _seed_draft(session: AsyncSession) -> uuid.UUID:
    course = DraftCourse(
        organization_id=DEFAULT_ORG_ID, title="Authored Course", status="drafting"
    )
    session.add(course)
    await session.flush()
    obj = DraftObjective(
        organization_id=DEFAULT_ORG_ID,
        draft_course_id=course.id,
        text="Understand the topic",
        week_index=1,
        order_index=0,
    )
    session.add(obj)
    await session.flush()
    item = DraftItem(
        organization_id=DEFAULT_ORG_ID,
        draft_course_id=course.id,
        kind=AssetKind.lesson_plan,
        title="Lesson one",
        content="Retrieval-augmented generation grounds answers in real content.",
        week_index=1,
        order_index=0,
    )
    session.add(item)
    await session.flush()
    session.add(
        DraftItemObjective(
            organization_id=DEFAULT_ORG_ID,
            draft_item_id=item.id,
            draft_objective_id=obj.id,
        )
    )
    await session.commit()
    return course.id


@asynccontextmanager
async def _transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    # The background ingest runner opens its own org-scoped session in prod; in
    # tests hand it the already-org-scoped test session so it makes no real
    # app-engine connection but still runs the full runner path (offline Fake).
    @asynccontextmanager
    async def _yield_session(_org_id):
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ingest_session_scope] = lambda: _yield_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _publish_and_gate(client, db_session, draft_id, users) -> uuid.UUID:
    """Publish a draft, then satisfy the QA + approval gate. Returns the CCR id."""
    author, reviewer, pm_approver, instructor_approver = users
    pub = await client.post(
        f"/api/v1/builder/courses/{draft_id}/publish",
        headers=_auth("architect", str(author.id)),
    )
    assert pub.status_code == 201, pub.text
    body = pub.json()
    ccr_id = body["ccr_id"]
    version_id = uuid.UUID(body["version_id"])

    await client.post(
        f"/api/v1/ccrs/{ccr_id}/qa",
        json={"dimension_scores": _FULL_SCORES, "verdict": "pass"},
        headers=_auth("qa_lead", str(reviewer.id)),
    )
    for role, sub in (
        ("program_manager", str(pm_approver.id)),
        ("instructor", str(instructor_approver.id)),
    ):
        await client.post(
            f"/api/v1/ccrs/{ccr_id}/approvals",
            json={"decision": "approve"},
            headers=_auth(role, sub),
        )
    return ccr_id, version_id


async def test_initial_release_triggers_ingest(db_session: AsyncSession):
    """Releasing a first-time authored course ingests its content into the index."""
    author = await _seed_user(db_session, role="architect")
    reviewer = await _seed_user(db_session, role="qa_lead")
    pm_approver = await _seed_user(db_session, role="program_manager")
    instructor_approver = await _seed_user(db_session, role="instructor")
    releaser = await _seed_user(db_session, role="program_manager")
    draft_id = await _seed_draft(db_session)
    users = (author, reviewer, pm_approver, instructor_approver)

    async with _transport(db_session) as client:
        ccr_id, version_id = await _publish_and_gate(
            client, db_session, draft_id, users
        )

        # No index before release.
        assert await _chunk_count(db_session, version_id) == 0

        rel = await client.post(
            f"/api/v1/ccrs/{ccr_id}/release",
            headers=_auth("program_manager", str(releaser.id)),
        )
        assert rel.status_code == 200, rel.text
        assert rel.json()["status"] == "active"

    # ASGITransport awaited the scheduled ingest → the index is now built for the
    # newly-active version, stamped with the releasing tenant.
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(ContentChunk).where(
                ContentChunk.curriculum_version_id == version_id
            )
        )
    ).scalars().all()
    assert rows, "initial release did not trigger ingest"
    for r in rows:
        assert r.organization_id == DEFAULT_ORG_ID
        assert r.curriculum_version_id == version_id

    # Idempotent: re-releasing is guarded, but re-running ingest for the version
    # (as a re-release would) does not duplicate — verified via the runner path.
    from app.core.retrieval.embedder import FakeEmbedder
    from app.core.retrieval.ingest_runner import run_ingest

    def _scope(_org_id):
        @asynccontextmanager
        async def _cm():
            yield db_session

        return _cm()

    before = await _chunk_count(db_session, version_id)
    await run_ingest(
        version_id, DEFAULT_ORG_ID, embedder=FakeEmbedder(), session_scope=_scope
    )
    db_session.expire_all()
    assert await _chunk_count(db_session, version_id) == before


# ---------------------------------------------------------------------------
# CCR-MERGE path (POST /ccrs/{id}/merge → fork())
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_backfilled_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    async with session_factory() as session:
        await seed(session)
        await backfill_content_model(session)
    yield engine
    await engine.dispose()


async def _first_org(engine) -> uuid.UUID:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(Organization.id))).scalars().first()


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def _user_by_role(session: AsyncSession, role: str) -> User:
    u = await session.scalar(select(User).where(User.role == role))
    assert u is not None, f"seed missing a {role} user"
    return u


async def test_merge_triggers_ingest(seeded_backfilled_engine):
    """Merging an approved CCR ingests the newly-forked active version's content."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await session.scalar(
                    select(Curriculum).where(Curriculum.slug == SEEDED_SLUG)
                )
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                change_set = ReleaseChangeSet(
                    bump="minor",
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/97/rag_lab",
                            kind=AssetKind.lab,
                            content="# RAG lab\nGround the tutor in retrieved chunks.",
                            section="Week 97: Bonus",
                            week_index=97,
                            order=0,
                        )
                    ],
                )
                ccr = await submit_ccr(
                    session,
                    curriculum_id=cur.id,
                    author_id=author.id,
                    title="Add a RAG lab",
                    rationale="ingest-on-merge test",
                    proposed_bump=BumpType.minor,
                    affected_kinds=set(),
                    instructor_override=True,
                )
                ccr.change_set = change_set.model_dump(mode="json")
                session.add(ccr)
                await session.flush()

                await record_qa(
                    session,
                    ccr=ccr,
                    reviewer_id=instructor.id,
                    dimension_scores=_QA_PASS,
                    verdict="pass",
                )
                await record_approval(
                    session, ccr=ccr, approver_id=instructor.id,
                    role="instructor", decision="approve",
                )
                await record_approval(
                    session, ccr=ccr, approver_id=second.id,
                    role="architect", decision="approve",
                )
                await session.flush()

                # Inject: background_tasks captured so we can run it, and a
                # session_scope yielding THIS org-scoped session (offline).
                bg = BackgroundTasks()

                def _scope(_org_id):
                    @asynccontextmanager
                    async def _cm():
                        yield session

                    return _cm()

                out = await merge(
                    ccr.id,
                    background_tasks=bg,
                    current=merger,
                    db=session,
                    session_scope=_scope,
                )
                new_version_id = out.version_id

                # No index yet — ingest was SCHEDULED, not run inline.
                assert await _chunk_count(session, new_version_id) == 0

                # Run the scheduled background task(s).
                await bg()

                session.expire_all()
                rows = (
                    await session.execute(
                        select(ContentChunk).where(
                            ContentChunk.curriculum_version_id == new_version_id
                        )
                    )
                ).scalars().all()
                assert rows, "merge did not trigger ingest for the new version"
                for r in rows:
                    assert r.organization_id == org_id
            finally:
                await session.close()
    finally:
        current_org.reset(token)
