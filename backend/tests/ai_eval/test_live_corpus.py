"""Tests for the LIVE SOTA-research adapter (V2 live field signal).

The live path is FULLY MOCKED — a ``FakeSearcher`` implements the ``WebSearcher``
seam and returns canned ``CorpusDoc``s. The real ``AIClient.web_search_corpus``
Anthropic call is NEVER made over the network: where it's exercised, its lazy
``client`` property is monkeypatched to a stub returning a canned response shaped
exactly like the documented web_search server-tool response. ZERO real network /
Anthropic calls in CI.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, WebSearcher
from app.ai.corpus import CuratedCorpusProvider, LiveCorpusProvider
from app.ai.schemas import CorpusDoc, GapFinding
from app.auth.jwt import create_access_token
from app.config import settings
from app.core.actors import ensure_ai_researcher
from app.database import get_db
from app.main import app
from app.models.sota import SotaSource
from app.routers.research import get_ai_extractor, get_corpus_provider

from tests.ai_eval.test_gap_research import (
    FakeExtractor,
    _seed_curriculum_with_version,
    _two_findings,
)


# ---------------------------------------------------------------------------
# Fakes for the live seams.
# ---------------------------------------------------------------------------


class FakeSearcher:
    """Implements the ``WebSearcher`` seam; returns canned live docs."""

    def __init__(self, docs: list[CorpusDoc]) -> None:
        self._docs = docs
        self.seen_query: str | None = None
        self.seen_max_results: int | None = None
        self.call_count = 0

    async def web_search_corpus(
        self, query: str, max_results: int
    ) -> list[CorpusDoc]:
        self.call_count += 1
        self.seen_query = query
        self.seen_max_results = max_results
        return self._docs[:max_results]


def _two_live_docs() -> list[CorpusDoc]:
    return [
        CorpusDoc(
            title="Agentic AI Engineer — Acme (2026)",
            kind="live_search",
            body="Hiring for MCP servers and agent observability.\n\nSource: https://jobs.example.com/1",
        ),
        CorpusDoc(
            title="State of Agentic AI 2026 — Vendor",
            kind="live_search",
            body="Tracing for agentic systems is now table stakes.\n\nSource: https://vendor.example.com/report",
        ),
    ]


# ---------------------------------------------------------------------------
# Seam-shape tests: WebSearcher Protocol + AIClient.web_search_corpus exist
# and are coroutine-typed (no network).
# ---------------------------------------------------------------------------


def test_aiclient_implements_websearcher_seam():
    # AIClient (constructed, never used over the network here) satisfies the
    # runtime-checkable WebSearcher Protocol.
    client = AIClient(api_key="test-not-used")
    assert isinstance(client, WebSearcher)


def test_web_search_corpus_is_a_coroutine_function():
    assert inspect.iscoroutinefunction(AIClient.web_search_corpus)
    assert inspect.iscoroutinefunction(WebSearcher.web_search_corpus)


@pytest.mark.asyncio
async def test_web_search_corpus_parses_server_tool_response(monkeypatch):
    """Exercise the REAL parsing logic against a canned, documented response
    shape — with the Anthropic client stubbed (no network)."""

    # Response content shaped per the web_search docs: a web_search_tool_result
    # block (web_search_result items) + text blocks carrying
    # web_search_result_location citations (cited_text = readable snippet).
    canned = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="I'll search.", citations=None),
            SimpleNamespace(
                type="web_search_tool_result",
                tool_use_id="srvtoolu_1",
                content=[
                    SimpleNamespace(
                        type="web_search_result",
                        url="https://jobs.example.com/1",
                        title="Agentic AI Engineer",
                        page_age="May 1, 2026",
                    ),
                    SimpleNamespace(
                        type="web_search_result",
                        url="https://vendor.example.com/report",
                        title="State of Agentic AI 2026",
                        page_age="April 2026",
                    ),
                ],
            ),
            SimpleNamespace(
                type="text",
                text="Findings...",
                citations=[
                    SimpleNamespace(
                        type="web_search_result_location",
                        url="https://jobs.example.com/1",
                        title="Agentic AI Engineer",
                        cited_text="experience with MCP servers required",
                    )
                ],
            ),
        ]
    )

    class _StubMessages:
        async def create(self, **kwargs):
            # Assert the documented tool identifier is passed.
            assert kwargs["tools"] == [
                {"type": "web_search_20260209", "name": "web_search"}
            ]
            return canned

    stub_client = SimpleNamespace(messages=_StubMessages())

    client = AIClient(api_key="test-not-used")
    monkeypatch.setattr(type(client), "client", property(lambda self: stub_client))

    docs = await client.web_search_corpus("AI Engineering", max_results=20)

    assert len(docs) == 2
    assert all(d.kind == "live_search" for d in docs)
    assert docs[0].title == "Agentic AI Engineer"
    # Snippet from the citation is folded into the first doc's body + URL.
    assert "MCP servers" in docs[0].body
    assert "https://jobs.example.com/1" in docs[0].body
    # The second source had no citation snippet — still gets the URL provenance.
    assert "https://vendor.example.com/report" in docs[1].body


@pytest.mark.asyncio
async def test_web_search_corpus_truncates_to_max_results(monkeypatch):
    canned = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="web_search_tool_result",
                tool_use_id="srvtoolu_1",
                content=[
                    SimpleNamespace(
                        type="web_search_result",
                        url=f"https://example.com/{i}",
                        title=f"Result {i}",
                        page_age="2026",
                    )
                    for i in range(5)
                ],
            ),
        ]
    )

    class _StubMessages:
        async def create(self, **kwargs):
            return canned

    stub_client = SimpleNamespace(messages=_StubMessages())
    client = AIClient(api_key="test-not-used")
    monkeypatch.setattr(type(client), "client", property(lambda self: stub_client))

    docs = await client.web_search_corpus("X", max_results=2)
    assert len(docs) == 2


# ---------------------------------------------------------------------------
# Error-path tests for web_search_corpus (zero network; client stubbed).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_corpus_pause_turn_raises(monkeypatch):
    """A paused agentic turn (stop_reason=pause_turn) delivers partial results
    silently — we surface it as a RuntimeError instead of returning a thin
    corpus."""
    canned = SimpleNamespace(stop_reason="pause_turn", content=[])

    class _StubMessages:
        async def create(self, **kwargs):
            return canned

    stub_client = SimpleNamespace(messages=_StubMessages())
    client = AIClient(api_key="test-not-used")
    monkeypatch.setattr(type(client), "client", property(lambda self: stub_client))

    with pytest.raises(RuntimeError, match="pause_turn"):
        await client.web_search_corpus("X", max_results=20)


@pytest.mark.asyncio
async def test_web_search_corpus_tool_error_block_returns_empty_and_logs(
    monkeypatch, caplog
):
    """A web_search_tool_result whose content is an error object (not a list)
    yields [] without crashing, and logs a warning carrying the error."""
    canned = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="text", text="Searching.", citations=None),
            SimpleNamespace(
                type="web_search_tool_result",
                tool_use_id="srvtoolu_1",
                content=SimpleNamespace(
                    type="web_search_tool_result_error",
                    error_code="too_many_requests",
                ),
            ),
        ],
    )

    class _StubMessages:
        async def create(self, **kwargs):
            return canned

    stub_client = SimpleNamespace(messages=_StubMessages())
    client = AIClient(api_key="test-not-used")
    monkeypatch.setattr(type(client), "client", property(lambda self: stub_client))

    with caplog.at_level("WARNING"):
        docs = await client.web_search_corpus("X", max_results=20)

    assert docs == []
    assert any("tool-error block" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_web_search_corpus_no_search_blocks_returns_empty(monkeypatch):
    """Claude answered without searching (no web_search_tool_result blocks) ->
    returns [] cleanly, no crash."""
    canned = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="text", text="I already know this.", citations=None),
        ],
    )

    class _StubMessages:
        async def create(self, **kwargs):
            return canned

    stub_client = SimpleNamespace(messages=_StubMessages())
    client = AIClient(api_key="test-not-used")
    monkeypatch.setattr(type(client), "client", property(lambda self: stub_client))

    docs = await client.web_search_corpus("X", max_results=20)
    assert docs == []


# ---------------------------------------------------------------------------
# Provider tests (Task 2 seam): curated default + live provider.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curated_provider_returns_existing_rows(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    db_session.add_all(
        [
            SotaSource(title="JD A", kind="job_posting", body="needs MCP"),
            SotaSource(title="JD B", kind="job_posting", body="needs tracing"),
        ]
    )
    await db_session.flush()

    rows = await CuratedCorpusProvider().fetch(db_session, cur)
    assert {r.title for r in rows} == {"JD A", "JD B"}
    assert all(r.kind == "job_posting" for r in rows)


@pytest.mark.asyncio
async def test_live_provider_persists_live_search_rows(db_session: AsyncSession):
    cur, _ = await _seed_curriculum_with_version(db_session)
    searcher = FakeSearcher(_two_live_docs())
    provider = LiveCorpusProvider(searcher, max_results=20)

    rows = await provider.fetch(db_session, cur)

    # The provider seeded the live search with the curriculum name (domain seed).
    assert searcher.seen_query == cur.name
    assert searcher.seen_max_results == 20
    assert len(rows) == 2
    assert all(r.kind == "live_search" for r in rows)

    # Rows were persisted (queryable) with provenance.
    persisted = (
        await db_session.execute(
            select(SotaSource).where(SotaSource.kind == "live_search")
        )
    ).scalars().all()
    assert len(persisted) == 2
    titles = {r.title for r in persisted}
    assert "Agentic AI Engineer — Acme (2026)" in titles


# ---------------------------------------------------------------------------
# Endpoint tests: ?live=true wiring, role-gating, and 503 fallback.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(
    session: AsyncSession,
    extractor,
    *,
    provider=None,
):
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ai_extractor] = lambda: extractor
    if provider is not None:
        app.dependency_overrides[get_corpus_provider] = lambda: provider
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str) -> dict:
    from tests.conftest import DEFAULT_ORG_ID

    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_live_endpoint_persists_and_drafts_from_live_docs(
    db_session: AsyncSession,
):
    cur, _ = await _seed_curriculum_with_version(db_session)
    extractor = FakeExtractor(_two_findings())
    searcher = FakeSearcher(_two_live_docs())
    provider = LiveCorpusProvider(searcher, max_results=20)

    async with _make_transport(db_session, extractor, provider=provider) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur.id}/research?live=true", headers=_auth("architect")
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body) == 2
    # The live searcher ran and the extractor saw the live docs.
    assert searcher.call_count == 1
    assert extractor.call_count == 1
    assert extractor.seen_corpus_docs is not None
    assert all(d.kind == "live_search" for d in extractor.seen_corpus_docs)

    # Live SotaSource rows were persisted with provenance.
    persisted = (
        await db_session.execute(
            select(SotaSource).where(SotaSource.kind == "live_search")
        )
    ).scalars().all()
    assert len(persisted) == 2

    # CCRs authored by the AI Researcher (not the human caller).
    ai_user = await ensure_ai_researcher(db_session)
    assert all(c["author_id"] == str(ai_user.id) for c in body)


@pytest.mark.asyncio
async def test_curated_default_path_unchanged(db_session: AsyncSession):
    """Without ?live, the curated provider runs — no live rows, normal drafts."""
    cur, _ = await _seed_curriculum_with_version(db_session)
    db_session.add(SotaSource(title="JD", kind="job_posting", body="needs MCP"))
    await db_session.flush()
    extractor = FakeExtractor(_two_findings())

    # No provider override — exercise the real get_corpus_provider (curated).
    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur.id}/research", headers=_auth("architect")
        )

    assert resp.status_code == 201, resp.text
    assert len(resp.json()) == 2
    live = (
        await db_session.execute(
            select(SotaSource).where(SotaSource.kind == "live_search")
        )
    ).scalars().all()
    assert live == []


@pytest.mark.asyncio
async def test_live_without_flag_or_key_returns_503(
    db_session: AsyncSession, monkeypatch
):
    """?live=true with LIVE_SOTA_ENABLED off (default) -> 503, no extractor call.

    No provider override here, so the REAL get_corpus_provider gate runs.
    """
    cur, _ = await _seed_curriculum_with_version(db_session)
    extractor = FakeExtractor(_two_findings())

    # Ensure the gate is closed regardless of ambient env.
    monkeypatch.setattr(settings, "LIVE_SOTA_ENABLED", False)

    async with _make_transport(db_session, extractor) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur.id}/research?live=true", headers=_auth("architect")
        )

    assert resp.status_code == 503, resp.text
    assert "live" in resp.json()["detail"].lower()
    assert extractor.call_count == 0


@pytest.mark.asyncio
async def test_live_enabled_but_no_key_returns_503(
    db_session: AsyncSession, monkeypatch
):
    """LIVE_SOTA_ENABLED on but no API key -> 503 via the real get_ai_extractor.

    The key gate now lives in get_ai_extractor (which research_gaps and
    get_corpus_provider both depend on), so this test must NOT override that
    dependency — it exercises the REAL keyless gate. Only get_db is overridden.
    """
    cur, _ = await _seed_curriculum_with_version(db_session)

    monkeypatch.setattr(settings, "LIVE_SOTA_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/curricula/{cur.id}/research?live=true",
                headers=_auth("architect"),
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503, resp.text
    assert "anthropic_api_key" in resp.json()["detail"].lower()
