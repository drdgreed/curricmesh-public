"""PR-style review → merge: the executable merge endpoint wired to fork().

Drives the real ``app.routers.approvals.merge`` handler against a freshly-seeded
+ back-filled schema (manifest live), mirroring the release/golden org-pinned
fixture pattern (P-006: seed + back-fill within the test on a dedicated engine).

The merge gate is :func:`app.core.workflow.engine.can_release` — a passing QA
review plus two distinct ``approve`` approvals including one instructor role.
Approvals/QA are set up via the engine helpers directly (not the HTTP routers).

What is asserted:
  * happy path — a CCR carrying a change-set (one added asset + a prerequisite
    edge), once approved, merges: bumps semver, activates a new version
    (member_count = parent + 1), the added node shows in the graph, and the CCR
    moves to the released/active status.
  * not approved → 409, nothing released.
  * no change-set → 400 (even when approved).
  * dangling edge → 422 (fail-closed: no new version).
  * self-approval is still blocked by the engine gate.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.manifest import active_curriculum_version, version_members
from app.core.versioning.semver import BumpType
from app.core.workflow.engine import record_approval, record_qa, submit_ccr
from app.core.workflow.rules import WorkflowError
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.org import Organization
from app.models.user import User
from app.models.workflow import ChangeRequest
from app.routers.approvals import merge, release_gate
from app.routers.graph import get_curriculum_graph
from app.schemas.release import EdgeSpecIn, NewAssetIn, ReleaseChangeSet
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

SEEDED_SLUG = "agentic-ai"

# Full passing QA scorecard (all six dimensions, in 1–5 range).
_QA_PASS = {
    "content_accuracy": 5,
    "alignment": 5,
    "prerequisites": 5,
    "consistency": 5,
    "instructor_support": 5,
    "student_experience": 5,
}


@pytest.fixture
async def seeded_backfilled_engine():
    """Dedicated engine on a freshly-seeded + back-filled schema (manifest live)."""
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


async def _curriculum(session: AsyncSession) -> Curriculum:
    cur = await session.scalar(select(Curriculum).where(Curriculum.slug == SEEDED_SLUG))
    assert cur is not None, "seed missing the agentic-ai curriculum"
    return cur


async def _user_by_role(session: AsyncSession, role: str) -> User:
    u = await session.scalar(select(User).where(User.role == role))
    assert u is not None, f"seed missing a {role} user"
    return u


async def _current_for(session: AsyncSession, role: str) -> dict[str, Any]:
    """A current-user dict backed by a real seeded user (history FK is satisfied)."""
    u = await _user_by_role(session, role)
    return {"sub": str(u.id), "role": role}


async def _active_member_count(session: AsyncSession, curriculum_id: uuid.UUID) -> int:
    cv = await active_curriculum_version(session, curriculum_id)
    assert cv is not None
    return len(await version_members(session, cv.id))


async def _make_ccr(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    author: User,
    change_set: ReleaseChangeSet | None,
) -> ChangeRequest:
    """Create a CCR via the engine, attaching an optional executable change-set."""
    # The seeded curriculum has an active cohort; instructor_override lets the
    # mid-cohort guard pass. The CCR's proposed_bump is independent of the
    # change_set bump that fork() actually applies at merge time.
    ccr = await submit_ccr(
        session,
        curriculum_id=curriculum_id,
        author_id=author.id,
        title="Add a bonus lab",
        rationale="PR-style merge test",
        proposed_bump=BumpType.minor,
        affected_kinds=set(),
        instructor_override=True,
    )
    if change_set is not None:
        ccr.change_set = change_set.model_dump(mode="json")
        session.add(ccr)
    await session.flush()
    return ccr


async def _approve_gate(
    session: AsyncSession, ccr: ChangeRequest, instructor: User, second: User
) -> None:
    """Satisfy can_release(): one passing QA + two distinct approvals (one instructor)."""
    await record_qa(
        session,
        ccr=ccr,
        reviewer_id=instructor.id,
        dimension_scores=_QA_PASS,
        verdict="pass",
    )
    await record_approval(
        session, ccr=ccr, approver_id=instructor.id, role="instructor", decision="approve"
    )
    await record_approval(
        session, ccr=ccr, approver_id=second.id, role="architect", decision="approve"
    )
    await session.flush()


# ---------------------------------------------------------------------------


async def test_merge_happy_path(seeded_backfilled_engine):
    """An approved CCR with a change-set merges, activating a new version."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                # author + two distinct non-author approvers (one instructor role).
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                before_members = await _active_member_count(session, cur.id)
                before_graph = await get_curriculum_graph(
                    cur.id, current=merger, db=session
                )

                cv = await active_curriculum_version(session, cur.id)
                members = await version_members(session, cv.id)
                anchor_key = members[0].lineage_key

                change_set = ReleaseChangeSet(
                    bump="minor",
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/99/bonus_lab",
                            kind=AssetKind.lab,
                            content="# Bonus lab\nAdded via PR-style merge.",
                            section="Week 99: Bonus",
                            week_index=99,
                            order=0,
                        )
                    ],
                    edges_added=[
                        EdgeSpecIn(
                            from_key=anchor_key,
                            to_key="agentic-ai/v1/99/bonus_lab",
                        )
                    ],
                )
                ccr = await _make_ccr(session, cur.id, author, change_set)
                await _approve_gate(session, ccr, instructor, second)

                out = await merge(ccr.id, current=merger, db=session)

                assert out.member_count == before_members + 1
                assert out.summary.added == 1
                assert out.summary.edges_added == 1
                assert out.status == "active"
                # minor bump → patch component is 0, and not the zero version
                assert out.semver.split(".")[2] == "0" and out.semver != "0.0.0"
                assert out.parent_version_id is not None

                # CCR moved to the released/active status.
                await session.refresh(ccr)
                assert ccr.status == LifecycleStatus.active

                # New version is active and the added node shows in the graph.
                after_graph = await get_curriculum_graph(
                    cur.id, current=merger, db=session
                )
                assert len(after_graph.nodes) == len(before_graph.nodes) + 1
                labels = {n.label for n in after_graph.nodes}
                assert "agentic-ai/v1/99/bonus_lab" in labels
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_merge_already_merged_is_409(seeded_backfilled_engine):
    """Merging a CCR a second time is rejected (idempotency guard)."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}
                change_set = ReleaseChangeSet(
                    bump="patch",
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/94/dup_lab",
                            kind=AssetKind.lab,
                            content="x",
                            section="Week 94",
                            week_index=94,
                            order=0,
                        )
                    ],
                )
                ccr = await _make_ccr(session, cur.id, author, change_set)
                await _approve_gate(session, ccr, instructor, second)
                await merge(ccr.id, current=merger, db=session)  # first merge ok
                with pytest.raises(HTTPException) as exc:
                    await merge(ccr.id, current=merger, db=session)
                assert exc.value.status_code == 409
                assert "already been merged" in str(exc.value.detail)
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_release_gate_reflects_progress(seeded_backfilled_engine):
    """The gate endpoint reports each requirement and flips can_release when met."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                viewer = {"sub": str(second.id), "role": "architect"}

                change_set = ReleaseChangeSet(
                    bump="patch",
                    changed=[],
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/95/gate_lab",
                            kind=AssetKind.lab,
                            content="x",
                            section="Week 95",
                            week_index=95,
                            order=0,
                        )
                    ],
                )
                ccr = await _make_ccr(session, cur.id, author, change_set)

                # Fresh CCR: change-set present, nothing else met.
                g0 = await release_gate(ccr.id, current=viewer, db=session)
                assert g0.has_change_set is True
                assert g0.qa_passed is False
                assert g0.approval_count == 0
                assert g0.has_instructor_approval is False
                assert g0.can_release is False

                # After QA pass + 2 approvals (one instructor): fully unlocked.
                await _approve_gate(session, ccr, instructor, second)
                g1 = await release_gate(ccr.id, current=viewer, db=session)
                assert g1.qa_passed is True
                assert g1.approval_count == 2
                assert g1.has_instructor_approval is True
                assert g1.can_release is True
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_merge_not_approved_is_409(seeded_backfilled_engine):
    """A CCR with a change-set but no approvals cannot be merged."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                merger = await _current_for(session, "architect")

                cur_id = cur.id
                before_active_id = (
                    await active_curriculum_version(session, cur_id)
                ).id
                change_set = ReleaseChangeSet(
                    bump="minor",
                    added=[
                        NewAssetIn(
                            lineage_key="agentic-ai/v1/99/unapproved_lab",
                            kind=AssetKind.lab,
                            content="nope",
                        )
                    ],
                )
                ccr = await _make_ccr(session, cur.id, author, change_set)

                with pytest.raises(HTTPException) as exc:
                    await merge(ccr.id, current=merger, db=session)
                assert exc.value.status_code == 409

                await session.rollback()
                after_active = await active_curriculum_version(session, cur_id)
                assert after_active.id == before_active_id
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_merge_no_change_set_is_400(seeded_backfilled_engine):
    """A description-only CCR (null change-set) cannot be merged, even if approved."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                ccr = await _make_ccr(session, cur.id, author, change_set=None)
                await _approve_gate(session, ccr, instructor, second)

                with pytest.raises(HTTPException) as exc:
                    await merge(ccr.id, current=merger, db=session)
                assert exc.value.status_code == 400
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_merge_dangling_edge_is_422(seeded_backfilled_engine):
    """An approved change-set with a dangling edge fails closed as 422."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                instructor = await _user_by_role(session, "instructor")
                second = await _user_by_role(session, "architect")
                merger = {"sub": str(second.id), "role": "architect"}

                cv = await active_curriculum_version(session, cur.id)
                members = await version_members(session, cv.id)
                real_key = members[0].lineage_key

                change_set = ReleaseChangeSet(
                    bump="patch",
                    edges_added=[
                        EdgeSpecIn(from_key="does/not/exist", to_key=real_key)
                    ],
                )
                ccr = await _make_ccr(session, cur.id, author, change_set)
                await _approve_gate(session, ccr, instructor, second)

                cur_id = cur.id
                before_active = cv.id
                with pytest.raises(HTTPException) as exc:
                    await merge(ccr.id, current=merger, db=session)
                assert exc.value.status_code == 422

                await session.rollback()
                after_active = await active_curriculum_version(session, cur_id)
                assert after_active.id == before_active
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_self_approval_blocked(seeded_backfilled_engine):
    """The engine gate rejects an author approving their own CCR (light check)."""
    engine = seeded_backfilled_engine
    org_id = await _first_org(engine)
    token = current_org.set(org_id)
    try:
        with use_org(org_id):
            session = await _open_org_session(engine, org_id)
            try:
                cur = await _curriculum(session)
                author = await _user_by_role(session, "program_manager")
                ccr = await _make_ccr(session, cur.id, author, change_set=None)

                with pytest.raises(WorkflowError):
                    await record_approval(
                        session,
                        ccr=ccr,
                        approver_id=author.id,
                        role="program_manager",
                        decision="approve",
                    )
            finally:
                await session.close()
    finally:
        current_org.reset(token)
