"""API tests for the S4 deck-serve endpoints (Slide System Port).

Two surfaces:
  * ``GET /api/v1/learn/courses/{enrollment_id}/decks`` — learner-gated,
    enrollment-scoped; returns the pinned version's decks with fresh presigned
    GET URLs; cross-tenant / cross-learner → 404; empty when no decks.
  * ``GET /api/v1/slides/versions/{version_id}/decks`` — author preview
    (architect/program_manager); returns a version's decks with presigned URLs;
    tenant-scoped.

Transport mirrors tests/learner/test_learn_api.py — an ASGITransport client with
``get_db`` / ``get_storage`` overridden and JWTs minted with an org claim. No real
render (S1's toolchain is never invoked) — decks are seeded directly.
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
from app.models.deck_artifact import DeckArtifact
from app.models.enums import AssetKind, LifecycleStatus
from tests.conftest import DEFAULT_ORG_ID


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
    token = create_access_token(sub=str(sub or uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


async def _seed_released_course(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a released course with one item. Returns (version_id, member_id)."""
    curriculum = Curriculum(name="Agentic AI", slug=f"agentic-{uuid.uuid4()}")
    db.add(curriculum)
    await db.flush()

    version = CurriculumVersion(
        curriculum_id=curriculum.id, major=1, minor=0, patch=0,
        status=LifecycleStatus.active,
    )
    db.add(version)
    await db.flush()
    curriculum.active_content_version_id = version.id

    asset = LineageAsset(kind=AssetKind.lesson_plan, lineage_key="intro")
    db.add(asset)
    await db.flush()
    content = ContentVersion(
        asset_id=asset.id, seq=1, content="Body.", content_hash="a" * 64
    )
    db.add(content)
    await db.flush()
    member = VersionMember(
        curriculum_version_id=version.id, asset_id=asset.id,
        asset_version_id=content.id, section="Week 1", week_index=0, order=0,
    )
    db.add(member)
    await db.flush()
    await db.commit()
    return version.id, member.id


async def _seed_deck(
    db: AsyncSession, version_id: uuid.UUID, member_id: uuid.UUID | None = None
) -> uuid.UUID:
    uid = uuid.uuid4()
    deck = DeckArtifact(
        curriculum_version_id=version_id,
        source_member_id=member_id,
        pdf_key=f"decks/{DEFAULT_ORG_ID}/{uid}/deck.pdf",
        pptx_key=f"decks/{DEFAULT_ORG_ID}/{uid}/deck.pptx",
        html_key=f"decks/{DEFAULT_ORG_ID}/{uid}/deck.html",
    )
    db.add(deck)
    await db.commit()
    await db.refresh(deck)
    return deck.id


async def _enroll(client: httpx.AsyncClient, version_id: uuid.UUID, learner: uuid.UUID) -> str:
    resp = await client.post(
        "/api/v1/learn/enroll",
        json={"curriculum_version_id": str(version_id)},
        headers=_auth("learner", sub=learner),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Learner serve endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_decks_returns_presigned_urls(db_session: AsyncSession):
    version_id, member_id = await _seed_released_course(db_session)
    await _seed_deck(db_session, version_id, member_id)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        eid = await _enroll(client, version_id, learner)
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}/decks",
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    decks = resp.json()
    assert len(decks) == 1
    d = decks[0]
    # Fresh presigned GET URLs embedding each artifact key — never the raw keys.
    assert d["html_url"].startswith("https://fake-storage/get/decks/")
    assert d["html_url"].endswith("/deck.html")
    assert d["pdf_url"].endswith("/deck.pdf")
    assert d["pptx_url"].endswith("/deck.pptx")
    assert d["source_member_id"] == str(member_id)
    assert d["status"] == "ready"
    # No stored key leaks into the response body.
    assert "html_key" not in d


@pytest.mark.asyncio
async def test_course_decks_empty_when_no_decks(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        eid = await _enroll(client, version_id, learner)
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}/decks",
            headers=_auth("learner", sub=learner),
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_course_decks_requires_learner_role(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        eid = await _enroll(client, version_id, learner)
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}/decks",
            headers=_auth("architect", sub=learner),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_course_decks_cross_learner_404(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    await _seed_deck(db_session, version_id)
    owner = uuid.uuid4()
    intruder = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        eid = await _enroll(client, version_id, owner)
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}/decks",
            headers=_auth("learner", sub=intruder),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_course_decks_cross_tenant_404(db_session: AsyncSession):
    """An enrollment in org A is invisible to a caller carrying org B's claim."""
    version_id, _ = await _seed_released_course(db_session)
    await _seed_deck(db_session, version_id)
    learner = uuid.uuid4()
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        eid = await _enroll(client, version_id, learner)
        resp = await client.get(
            f"/api/v1/learn/courses/{eid}/decks",
            headers=_auth("learner", sub=learner, org=uuid.uuid4()),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Author preview endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_preview_lists_version_decks(db_session: AsyncSession):
    version_id, member_id = await _seed_released_course(db_session)
    await _seed_deck(db_session, version_id, member_id)
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        resp = await client.get(
            f"/api/v1/slides/versions/{version_id}/decks",
            headers=_auth("architect"),
        )
    assert resp.status_code == 200, resp.text
    decks = resp.json()
    assert len(decks) == 1
    assert decks[0]["curriculum_version_id"] == str(version_id)
    assert decks[0]["html_url"].startswith("https://fake-storage/get/")


@pytest.mark.asyncio
async def test_author_preview_requires_author_role(db_session: AsyncSession):
    version_id, _ = await _seed_released_course(db_session)
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        resp = await client.get(
            f"/api/v1/slides/versions/{version_id}/decks",
            headers=_auth("learner"),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_author_preview_cross_tenant_empty(db_session: AsyncSession):
    """A version in org A is invisible to an author carrying org B's claim."""
    version_id, _ = await _seed_released_course(db_session)
    await _seed_deck(db_session, version_id)
    fake = FakeStorageBackend()
    async with _make_transport(db_session, fake) as client:
        resp = await client.get(
            f"/api/v1/slides/versions/{version_id}/decks",
            headers=_auth("architect", org=uuid.uuid4()),
        )
    assert resp.status_code == 200
    assert resp.json() == []
