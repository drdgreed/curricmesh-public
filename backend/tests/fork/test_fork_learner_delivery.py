"""Regression guard: a FORKED, released version delivers non-empty content.

This locks the "blank lesson body in the Course Player" class of bug at the exact
seam the demo hits: the learner is enrolled in the version ``_demo_enrich`` releases
(a ``fork()`` of v1.0.0 → v1.1.0), so if the fork's *structural sharing* ever stopped
carrying the parent's content-full ``ContentVersion`` rows forward, every lesson body
would render blank even though the item metadata (section / week / kind) still shows.

The test mirrors ``seed._demo_enrich`` faithfully — change one ``lesson_plan`` (new
body), add one capstone ``lab`` (new body + a prerequisite edge) — activates the fork,
enrolls a learner, and reads the course through the **real** ``/learn/courses/{eid}``
endpoint. It then asserts EVERY delivered ``CourseItem.content`` is non-empty:

* structurally-shared members must serve the parent's carried-forward body,
* the changed member must serve its new body,
* the added member must serve its own body.

Runs against the same freshly seeded + back-filled schema the rest of the fork suite
uses (``seeded_engine``), so it exercises real seed data, not a hand-built fixture.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.auth.jwt import create_access_token
from app.core.fork import (
    Bump,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    NewAsset,
    fork,
)
from app.core.manifest import active_curriculum_version, version_members
from app.database import get_db
from app.main import app
from app.media.storage import FakeStorageBackend, get_storage
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.learner import Enrollment
from tests.fork.conftest import org_for_slug, org_session

SLUG = "agentic-ai"


@pytest.mark.asyncio
async def test_forked_release_delivers_nonempty_content_to_learner(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)

    async with org_session(seeded_engine, oid) as s:
        curriculum_id = await s.scalar(
            Curriculum.__table__.select().with_only_columns(Curriculum.id).where(
                Curriculum.slug == SLUG
            )
        )
        parent = await active_curriculum_version(s, curriculum_id)
        parent_members = await version_members(s, parent.id)
        assert parent_members, "seed precondition: parent version has members"

        # Mirror _demo_enrich: change a lesson_plan, add a capstone lab that
        # depends on it. Structural sharing must carry every OTHER member forward.
        prefix = "/".join(parent_members[0].lineage_key.split("/")[:2])
        changed = next(
            (m for m in parent_members if "lesson_plan" in m.lineage_key),
            parent_members[0],
        )
        new_key = f"{prefix}/capstone/integration_lab"
        changes = ForkChanges(
            changed={
                changed.lineage_key: ContentEdit(
                    content="# Lesson Plan (v1.1.0)\n\nRevised body with real content."
                )
            },
            added=[
                NewAsset(
                    lineage_key=new_key,
                    kind=AssetKind.lab,
                    content="# Capstone Integration Lab\n\nApply the pipeline end-to-end.",
                    section="Capstone: Integration",
                    week_index=99,
                    order=0,
                )
            ],
            edges_added=[EdgeSpec(from_key=changed.lineage_key, to_key=new_key)],
        )
        new_version = await fork(s, curriculum_id, bump=Bump.minor, changes=changes)

        # Enroll a learner in the freshly-released (now-active) forked version.
        learner_id = uuid.uuid4()
        s.add(
            Enrollment(
                learner_id=learner_id, curriculum_version_id=new_version.id
            )
        )
        await s.commit()
        enrollment_id = await s.scalar(
            Enrollment.__table__.select()
            .with_only_columns(Enrollment.id)
            .where(Enrollment.learner_id == learner_id)
        )

        # Read the course through the real endpoint (same session drives get_db;
        # tenant_context binds current_org from the JWT's org claim).
        async def _override_get_db():
            yield s

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_storage] = lambda: FakeStorageBackend()
        try:
            headers = {
                "Authorization": "Bearer "
                + create_access_token(sub=str(learner_id), role="learner", org=str(oid))
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    f"/api/v1/learn/courses/{enrollment_id}", headers=headers
                )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]

    # The fork adds exactly one member (the capstone) to the shared manifest.
    assert body["total_items"] == len(parent_members) + 1
    assert len(items) == len(parent_members) + 1

    # THE GUARD: not a single lesson body may be blank in the Course Player.
    blank = [i["lineage_key"] for i in items if not (i["content"] or "").strip()]
    assert not blank, f"forked version served blank content for: {blank}"

    by_key = {i["lineage_key"]: i for i in items}
    # The changed member serves its NEW body; the added member serves its own body.
    assert "v1.1.0" in by_key[changed.lineage_key]["content"]
    assert "Capstone Integration Lab" in by_key[new_key]["content"]
    # A structurally-shared (unchanged) member still carries real content forward.
    shared = next(
        k for k in by_key if k not in (changed.lineage_key, new_key)
    )
    assert by_key[shared]["content"].strip()
