"""End-to-end acceptance: enroll -> consume -> complete a released course.

Walks the full self-paced learner lifecycle through the HTTP API in one flow,
proving the slice hangs together: an invited learner is enrolled by an admin,
browses the catalog, reads the pinned course structure with presigned media,
opens each item, submits an assessment, marks every item complete, and the
course flips to ``completed`` — with the version-pin holding across a re-release.
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
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from tests.conftest import DEFAULT_ORG_ID
from tests.learner.test_learn_api import _seed_released_course


@asynccontextmanager
async def _transport(session: AsyncSession, storage: FakeStorageBackend):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str, sub: uuid.UUID) -> dict:
    return {
        "Authorization": f"Bearer {create_access_token(sub=str(sub), role=role, org=DEFAULT_ORG_ID)}"
    }


@pytest.mark.asyncio
async def test_enroll_consume_complete_e2e(db_session: AsyncSession):
    version_id, member_ids = await _seed_released_course(
        db_session, n_items=3, with_media=True
    )
    admin = uuid.uuid4()
    learner = uuid.uuid4()
    fake = FakeStorageBackend()

    async with _transport(db_session, fake) as client:
        # 1. Invited-only: an admin enrolls the learner (pins the released version).
        enroll = await client.post(
            "/api/v1/learn/admin/enroll",
            json={
                "learner_id": str(learner),
                "curriculum_version_id": str(version_id),
            },
            headers=_auth("architect", admin),
        )
        assert enroll.status_code == 201, enroll.text
        eid = enroll.json()["id"]
        assert enroll.json()["status"] == "active"

        # 2. The learner sees the released course in the catalog.
        catalog = await client.get("/api/v1/learn/catalog", headers=_auth("learner", learner))
        assert catalog.status_code == 200
        assert str(version_id) in [c["curriculum_version_id"] for c in catalog.json()]

        # 3. The learner reads the pinned structure — 3 ordered items, each with
        #    a presigned media URL.
        course = await client.get(
            f"/api/v1/learn/courses/{eid}", headers=_auth("learner", learner)
        )
        assert course.status_code == 200
        items = course.json()["items"]
        assert [i["member_id"] for i in items] == [str(m) for m in member_ids]
        assert all(i["media"][0]["url"].startswith("https://fake-storage/get/") for i in items)

        # 4. Consume: open each item, submit an assessment response, mark complete.
        for idx, member_id in enumerate(member_ids):
            item = await client.get(
                f"/api/v1/learn/items/{eid}/{member_id}", headers=_auth("learner", learner)
            )
            assert item.status_code == 200

            sub = await client.post(
                f"/api/v1/learn/submit/{eid}/{member_id}",
                json={"response_text": f"answer {idx}"},
                headers=_auth("learner", learner),
            )
            assert sub.status_code == 201

            prog = await client.post(
                f"/api/v1/learn/progress/{eid}/{member_id}",
                json={"status": "complete"},
                headers=_auth("learner", learner),
            )
            assert prog.status_code == 200
            # Only the final item flips the course to completed.
            expected = "completed" if idx == len(member_ids) - 1 else "active"
            assert prog.json()["enrollment_status"] == expected

        # 5. The enrollments summary reflects completion.
        summary = await client.get(
            "/api/v1/learn/enrollments", headers=_auth("learner", learner)
        )
        row = summary.json()[0]
        assert row["status"] == "completed"
        assert row["completed_items"] == 3
        assert row["total_items"] == 3
        assert row["completed_at"] is not None

    # 6. Version-pin holds: a later re-release for the same curriculum does not
    #    move the learner's pinned version.
    version = await db_session.get(CurriculumVersion, version_id)
    v2 = CurriculumVersion(
        curriculum_id=version.curriculum_id,
        major=2,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
        parent_version_id=version_id,
    )
    db_session.add(v2)
    await db_session.flush()
    curriculum = await db_session.get(Curriculum, version.curriculum_id)
    curriculum.active_content_version_id = v2.id
    await db_session.commit()

    async with _transport(db_session, fake) as client:
        course = await client.get(
            f"/api/v1/learn/courses/{eid}", headers=_auth("learner", learner)
        )
        # Still the ORIGINAL pinned version, unchanged by the re-release.
        assert course.json()["curriculum_version_id"] == str(version_id)
