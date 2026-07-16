"""retrieve() tests + CRITICAL isolation (Phase B retrieval infra, Task 4).

Covers, against the real Postgres schema with pgvector:
  - top-k cosine retrieval returns the exact-match chunk first (FakeEmbedder is
    deterministic, so an exact-text query lands at cosine distance 0);
  - k bounds the result count;
  - e2e ingest → retrieve;
  - CRITICAL version isolation: version A's chunks are NEVER returned for a
    retrieve scoped to version B (same tenant);
  - CRITICAL tenant isolation: one org's chunks are NEVER returned for another
    org — even when the caller passes the other org's version id.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import FakeEmbedder
from app.core.retrieval.ingest import ingest_version
from app.core.retrieval.retrieve import retrieve
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID
from tests.retrieval._helpers import seed_version_with_members


@pytest.mark.asyncio
async def test_retrieve_returns_exact_match_first(db_session: AsyncSession):
    # Single-chunk members so an exact-text query maps to one chunk at dist 0.
    version = await seed_version_with_members(
        db_session,
        texts=[
            "reinforcement learning from human feedback",
            "retrieval augmented generation over course content",
            "vector databases and approximate nearest neighbours",
        ],
    )
    emb = FakeEmbedder()
    await ingest_version(db_session, version.id, emb)

    hits = await retrieve(
        db_session,
        version.id,
        "retrieval augmented generation over course content",
        k=3,
        embedder=emb,
    )
    assert hits, "expected at least one hit"
    assert hits[0].text == "retrieval augmented generation over course content"


@pytest.mark.asyncio
async def test_retrieve_respects_k(db_session: AsyncSession):
    version = await seed_version_with_members(
        db_session, texts=[f"topic number {i}" for i in range(6)]
    )
    emb = FakeEmbedder()
    await ingest_version(db_session, version.id, emb)
    hits = await retrieve(db_session, version.id, "topic number 2", k=2, embedder=emb)
    assert len(hits) == 2


@pytest.mark.asyncio
async def test_retrieve_empty_index_returns_nothing(db_session: AsyncSession):
    version = await seed_version_with_members(db_session, texts=["something"])
    # Note: NOT ingested.
    emb = FakeEmbedder()
    hits = await retrieve(db_session, version.id, "something", k=5, embedder=emb)
    assert hits == []


@pytest.mark.asyncio
async def test_critical_version_isolation(db_session: AsyncSession):
    """Version A's chunks are NEVER returned for a retrieve scoped to version B."""
    emb = FakeEmbedder()
    marker = "unique-marker-only-in-version-a xyzzy"
    version_a = await seed_version_with_members(db_session, texts=[marker])
    version_b = await seed_version_with_members(
        db_session, texts=["completely different content for version b"]
    )
    await ingest_version(db_session, version_a.id, emb)
    await ingest_version(db_session, version_b.id, emb)

    # Query B with A's exact text — must NOT surface A's chunk.
    hits_b = await retrieve(db_session, version_b.id, marker, k=10, embedder=emb)
    assert all(h.curriculum_version_id == version_b.id for h in hits_b)
    assert all(marker != h.text for h in hits_b)

    # Query A with its own text — DOES surface A's chunk (sanity: the marker
    # exists and is retrievable within its own version).
    hits_a = await retrieve(db_session, version_a.id, marker, k=10, embedder=emb)
    assert any(h.text == marker for h in hits_a)
    assert all(h.curriculum_version_id == version_a.id for h in hits_a)


@pytest.mark.asyncio
async def test_critical_tenant_isolation(db_session: AsyncSession):
    """Org A's chunks are NEVER returned for org B — even with A's version id.

    The app-layer tenant filter (+ RLS) scopes ContentChunk reads to the caller's
    org, so passing another tenant's version_id yields nothing rather than a
    cross-tenant leak.
    """
    emb = FakeEmbedder()
    secret = "org-a-confidential-curriculum-secret"

    # Seed + ingest a version under DEFAULT_ORG (org A).
    version_a = await seed_version_with_members(db_session, texts=[secret])
    await ingest_version(db_session, version_a.id, emb)
    await db_session.commit()

    # A second tenant. organizations is NOT RLS-scoped → unconstrained insert.
    org_b = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
        {"id": str(org_b), "name": "Org B"},
    )
    await db_session.commit()

    # Under org B's context, retrieve against org A's version id. The tenant
    # filter reads current_org from the ContextVar; also move the RLS GUC so the
    # DB-layer policy agrees (defense in depth).
    await db_session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_b)},
    )
    with use_org(org_b):
        hits = await retrieve(db_session, version_a.id, secret, k=10, embedder=emb)
    assert hits == [], "org B must not see org A's chunks"

    # Restore DEFAULT_ORG GUC for clean teardown.
    await db_session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(DEFAULT_ORG_ID)},
    )
