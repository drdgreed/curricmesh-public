"""API tests for the learner-delivery router (Phase 2, Foundation 1).

Transport mirrors tests/media/test_media_api.py: an ASGITransport AsyncClient
with get_db (always) and get_storage (when media is presigned) overridden, and
JWTs minted via create_access_token carrying the org claim.

Covers: role gating (learner vs non-learner), catalog, self-enroll (+ dup 409 +
non-released 404), enrollments summary, course structure w/ presigned media +
ordering, single item, progress recompute → course completion, assessment
submit, admin invited-only enroll, and cross-learner / cross-tenant isolation.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.media.storage import FakeStorageBackend, get_storage
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.learner import Enrollment
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Transport + auth helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession, storage: FakeStorageBackend | None = None):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    if storage is not None:
        app.dependency_overrides[get_storage] = lambda: storage
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str, sub: uuid.UUID | None = None, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(
        sub=str(sub or uuid.uuid4()), role=role, org=org
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _seed_released_course(
    db: AsyncSession, n_items: int = 2, with_media: bool = False
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Create a released course (Curriculum -> active CurriculumVersion + items).

    Returns (curriculum_version_id, [member_id, ...]) in weekly order.
    """
    curriculum = Curriculum(name="Agentic AI", slug=f"agentic-{uuid.uuid4()}")
    db.add(curriculum)
    await db.flush()

    version = CurriculumVersion(
        curriculum_id=curriculum.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    db.add(version)
    await db.flush()

    # Mark it the curriculum's current released version (catalog source of truth).
    curriculum.active_content_version_id = version.id

    member_ids: list[uuid.UUID] = []
    for i in range(n_items):
        asset = LineageAsset(kind=AssetKind.lesson_plan, lineage_key=f"item-{i}")
        db.add(asset)
        await db.flush()
        media_refs = None
        if with_media:
            media_refs = [
                {
                    "id": str(uuid.uuid4()),
                    "storage_key": f"{DEFAULT_ORG_ID}/media/{uuid.uuid4()}/clip{i}.mp4",
                    "kind": "video",
                    "filename": f"clip{i}.mp4",
                }
            ]
        content = ContentVersion(
            asset_id=asset.id,
            seq=1,
            content=f"Lesson {i} body.",
            content_hash=f"{i:064d}",
            media_refs=media_refs,
        )
        db.add(content)
        await db.flush()
        member = VersionMember(
            curriculum_version_id=version.id,
            asset_id=asset.id,
            asset_version_id=content.id,
            section=f"Week {i}",
            week_index=i,
            order=0,
        )
        db.add(member)
        await db.flush()
        member_ids.append(member.id)

    await db.commit()
    return version.id, member_ids


# ---------------------------------------------------------------------------
# Role gating (Task 2: the learner role)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_requires_learner_role(db_session: AsyncSession):
    await _seed_released_course(db_session)
    async with _make_transport(db_session) as client:
        ok = await client.get("/api/v1/learn/catalog", headers=_auth("learner"))
        forbidden = await client.get(
            "/api/v1/learn/catalog", headers=_auth("architect")
        )
    assert ok.status_code == 200, ok.text
    assert len(ok.json()) == 1
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Enroll (self) + catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_pins_version_and_rejects_duplicate(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    learner = uuid.uuid4()
    async with _make_transport(db_session) as client:
        first = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        dup = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["curriculum_version_id"] == str(version_id)
    assert body["learner_id"] == str(learner)
    assert body["total_items"] == 2
    assert body["completed_items"] == 0
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_enroll_non_released_version_404(db_session: AsyncSession):
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(uuid.uuid4())},
            headers=_auth("learner"),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_enrollments_lists_only_my_courses(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    me = uuid.uuid4()
    other = uuid.uuid4()
    async with _make_transport(db_session) as client:
        await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=me),
        )
        await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=other),
        )
        mine = await client.get(
            "/api/v1/learn/enrollments", headers=_auth("learner", sub=me)
        )
    assert mine.status_code == 200
    rows = mine.json()
    assert len(rows) == 1
    assert rows[0]["learner_id"] == str(me)


# ---------------------------------------------------------------------------
# Course structure + item (presigned media, ordering, isolation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_structure_presigns_media_and_orders(db_session: AsyncSession):
    version_id, member_ids = await _seed_released_course(
        db_session, n_items=2, with_media=True
    )
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        eid = enroll.json()["id"]
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}", headers=_auth("learner", sub=learner)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_items"] == 2
    # Ordered by (week_index, order).
    assert [i["member_id"] for i in body["items"]] == [str(m) for m in member_ids]
    # Every item has a presigned media URL embedding its storage key.
    for item in body["items"]:
        assert len(item["media"]) == 1
        assert item["media"][0]["url"].startswith("https://fake-storage/get/")


@pytest.mark.asyncio
async def test_course_structure_cross_learner_404(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    owner = uuid.uuid4()
    intruder = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=owner),
        )
        eid = enroll.json()["id"]
        # A different learner in the SAME tenant cannot read the enrollment.
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}", headers=_auth("learner", sub=intruder)
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_course_structure_cross_tenant_404(db_session: AsyncSession):
    """An enrollment in org A is invisible to a caller in org B (auto-filter)."""
    version_id, _ = await _seed_released_course(db_session)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        eid = enroll.json()["id"]
        # Same learner sub, but a DIFFERENT tenant org claim → 404.
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}",
            headers=_auth("learner", sub=learner, org=uuid.uuid4()),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_item_and_unknown_member_404(db_session: AsyncSession):
    version_id, member_ids = await _seed_released_course(
        db_session, n_items=1, with_media=True
    )
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        eid = enroll.json()["id"]
        ok = await client.get(
            f"/api/v1/learn/items/{eid}/{member_ids[0]}",
            headers=_auth("learner", sub=learner),
        )
        missing = await client.get(
            f"/api/v1/learn/items/{eid}/{uuid.uuid4()}",
            headers=_auth("learner", sub=learner),
        )
    assert ok.status_code == 200, ok.text
    assert ok.json()["content"] == "Lesson 0 body."
    assert ok.json()["progress_status"] == "not_started"
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Progress → completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_completes_course_when_all_items_done(db_session: AsyncSession):
    version_id, member_ids = await _seed_released_course(db_session, n_items=2)
    learner = uuid.uuid4()
    async with _make_transport(db_session) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        eid = enroll.json()["id"]
        # Complete item 0 — course still active.
        r0 = await client.post(
            f"/api/v1/learn/progress/{eid}/{member_ids[0]}",
            json={"status": "complete"},
            headers=_auth("learner", sub=learner),
        )
        # Complete item 1 — course now completed.
        r1 = await client.post(
            f"/api/v1/learn/progress/{eid}/{member_ids[1]}",
            json={"status": "complete"},
            headers=_auth("learner", sub=learner),
        )
    assert r0.status_code == 200, r0.text
    assert r0.json()["enrollment_status"] == "active"
    assert r0.json()["completed_items"] == 1
    assert r1.json()["enrollment_status"] == "completed"
    assert r1.json()["completed_items"] == 2
    assert r1.json()["total_items"] == 2


# ---------------------------------------------------------------------------
# Assessment submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_assessment_stores_response(db_session: AsyncSession):
    version_id, member_ids = await _seed_released_course(db_session, n_items=1)
    learner = uuid.uuid4()
    async with _make_transport(db_session) as client:
        enroll = await client.post(
            "/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=_auth("learner", sub=learner),
        )
        eid = enroll.json()["id"]
        resp = await client.post(
            f"/api/v1/learn/submit/{eid}/{member_ids[0]}",
            json={"response_text": "My reasoned answer."},
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content_member_id"] == str(member_ids[0])
    assert body["score"] is None
    assert body["feedback"] is None


# ---------------------------------------------------------------------------
# Admin invited-only enroll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_enroll_invited_only(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    target_learner = uuid.uuid4()
    async with _make_transport(db_session) as client:
        # Admin (architect) enrolls a named learner.
        ok = await client.post(
            "/api/v1/learn/admin/enroll",
            json={
                "learner_id": str(target_learner),
                "curriculum_version_id": str(version_id),
            },
            headers=_auth("architect"),
        )
        # A learner cannot use the admin path.
        forbidden = await client.post(
            "/api/v1/learn/admin/enroll",
            json={
                "learner_id": str(uuid.uuid4()),
                "curriculum_version_id": str(version_id),
            },
            headers=_auth("learner"),
        )
    assert ok.status_code == 201, ok.text
    assert ok.json()["learner_id"] == str(target_learner)
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Admin-enrolled learner can then consume (the invited learner sees the course)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_enrolled_learner_can_read_own_course(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        enroll = await client.post(
            "/api/v1/learn/admin/enroll",
            json={
                "learner_id": str(learner),
                "curriculum_version_id": str(version_id),
            },
            headers=_auth("architect"),
        )
        eid = enroll.json()["id"]
        # The invited learner reads their own enrollment.
        mine = await client.get(
            "/api/v1/learn/enrollments", headers=_auth("learner", sub=learner)
        )
        course = await client.get(
            f"/api/v1/learn/courses/{eid}", headers=_auth("learner", sub=learner)
        )
    assert len(mine.json()) == 1
    assert course.status_code == 200
