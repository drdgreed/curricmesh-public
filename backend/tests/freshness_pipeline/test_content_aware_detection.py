"""Tests for content-aware gap detection (Task 3).

Verifies:
1. covered_content=None (absent or explicit) → user AND system prompts
   byte-identical to the baseline (backward-compat proof).
2. covered_content=[...] → CURRICULUM CONTENT CARDS appended to user prompt;
   verbatim suffix present in system prompt.
3. Runner calls build_content_cards and passes the result as covered_content
   to the extractor.
4. build_content_cards raising → run completes ok, extractor receives
   covered_content=None, stats["content_cards_failed"]==1.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.ai.schemas import CorpusDoc, GapFinding, GapReport, NetBenefitAssessment
from app.freshness_pipeline import PipelineSignal
from app.freshness_pipeline.runner import run_org
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.freshness_pipeline import PipelineSeen
from app.models.structure import Module
from app.models.version import Version
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_runner import FakeRaisingGenerator


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# This must match the verbatim suffix in app/ai/client._CONTENT_AWARE_SYSTEM_SUFFIX
# (minus the leading newline, which is just padding — the substring check still hits).
_VERBATIM_SUFFIX = (
    "When content cards are provided, judge coverage from what the content "
    "actually teaches rather than from titles alone."
)

_SAMPLE_CARDS = [
    {
        "lineage_key": "intro-to-ml",
        "kind": "lesson_plan",
        "section": "Week 1",
        "week_index": 1,
        "first_line": "Introduction to Machine Learning",
        "excerpt": (
            "Machine learning enables computers to learn from data "
            "without being explicitly programmed."
        ),
        "headings": ["What is ML?", "Types of Learning"],
        "word_count": 1200,
    }
]

_COVERED_TOPICS = ["Python", "REST APIs"]
_CORPUS = [CorpusDoc(title="Job posting", kind="job_posting", body="Need ML skills")]


# ---------------------------------------------------------------------------
# Unit tests (no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_prompts_byte_identical(monkeypatch):
    """covered_content absent and covered_content=None yield identical prompts."""
    client = AIClient(api_key="test-key")
    captured: list[dict] = []

    async def _fake_parse(*, system, user, output_format):
        captured.append({"system": system, "user": user})
        return GapReport(findings=[])

    monkeypatch.setattr(client, "_parse", _fake_parse)

    # Old-style call (no kwarg at all).
    await client.extract_gaps(_COVERED_TOPICS, _CORPUS)
    # New-style call with explicit None.
    await client.extract_gaps(_COVERED_TOPICS, _CORPUS, covered_content=None)

    assert len(captured) == 2
    assert captured[0]["system"] == captured[1]["system"], (
        "system prompt must be byte-identical when covered_content is None"
    )
    assert captured[0]["user"] == captured[1]["user"], (
        "user prompt must be byte-identical when covered_content is None"
    )


@pytest.mark.asyncio
async def test_cards_append_to_user_and_extend_system(monkeypatch):
    """With cards: user has CURRICULUM CONTENT CARDS block; system has verbatim suffix."""
    client = AIClient(api_key="test-key")
    captured: list[dict] = []

    async def _fake_parse(*, system, user, output_format):
        captured.append({"system": system, "user": user})
        return GapReport(findings=[])

    monkeypatch.setattr(client, "_parse", _fake_parse)

    await client.extract_gaps(_COVERED_TOPICS, _CORPUS, covered_content=_SAMPLE_CARDS)

    assert len(captured) == 1
    # User prompt must contain the CURRICULUM CONTENT CARDS header.
    assert "CURRICULUM CONTENT CARDS" in captured[0]["user"], (
        "User prompt missing CURRICULUM CONTENT CARDS section"
    )
    # The card excerpt must appear in the serialised JSON block.
    assert _SAMPLE_CARDS[0]["excerpt"] in captured[0]["user"], (
        "Card excerpt must appear in the user prompt"
    )
    # System prompt must carry the verbatim suffix.
    assert _VERBATIM_SUFFIX in captured[0]["system"], (
        "Verbatim content-aware system-prompt suffix missing when cards provided"
    )


# ---------------------------------------------------------------------------
# Runner integration tests (require DB)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_factory(session: AsyncSession):
    """Minimal session factory shim for run_org tests."""
    yield session


def _industry_signal(n: int) -> PipelineSignal:
    return PipelineSignal(
        id=f"cad-{n}",
        source_kind="industry_news",
        source="test-source",
        title=f"CAD signal {n}",
        url=f"https://example.com/cad/{n}",
        detail=f"Detail {n}",
        captured_at="2026-07-05T00:00:00Z",
    )


def _make_finding(topic: str) -> GapFinding:
    return GapFinding(
        topic=topic,
        coverage_status="missing",
        evidence=["test evidence"],
        proposed_bump="patch",
        rationale=f"Test rationale for {topic}",
    )


class CapturingExtractor:
    """Records the covered_content kwarg passed by the runner."""

    # Sentinel so tests can distinguish "never called" from "called with None".
    _NOT_SET = object()

    def __init__(self, findings: list[GapFinding]) -> None:
        self._findings = findings
        self.received_covered_content = self._NOT_SET

    async def extract_gaps(
        self,
        covered_topics,
        corpus_docs,
        covered_content=None,
    ) -> list[GapFinding]:
        self.received_covered_content = covered_content
        return self._findings


class FakeAdoptAllJudge:
    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment:
        return NetBenefitAssessment(
            evidence_strength=0.9,
            demand_signal=0.9,
            learner_value=0.9,
            curriculum_fit=0.9,
            effort_cost=0.9,
            urgency=0.9,
            competitive_signal=0.9,
            recommendation="adopt_now",
            confidence=0.9,
            rationale="Canned adopt.",
        )


async def _seed_curriculum(session: AsyncSession) -> tuple[Curriculum, Version]:
    cur = Curriculum(name="CAD Test", slug=f"cad-test-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    ver = Version(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    session.add(ver)
    await session.flush()

    session.add(Module(version_id=ver.id, index=0, focus="Python Fundamentals"))
    await session.flush()

    cur.current_version_id = ver.id
    session.add(cur)
    await session.flush()

    return cur, ver


async def _mark_not_first_run(session: AsyncSession) -> None:
    session.add(PipelineSeen(signal_id="cad-prior-run-marker"))
    await session.flush()


@pytest.mark.asyncio
async def test_runner_passes_cards_through(db_session: AsyncSession, monkeypatch):
    """Runner calls build_content_cards and passes result as covered_content."""
    await _seed_curriculum(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    async def _fake_build_content_cards(session, curriculum):
        return _SAMPLE_CARDS

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr(
        "app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item
    )
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)
    monkeypatch.setattr(
        "app.freshness_pipeline.runner.build_content_cards", _fake_build_content_cards
    )

    extractor = CapturingExtractor([_make_finding("ML Observability")])

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok"
    assert extractor.received_covered_content is not CapturingExtractor._NOT_SET, (
        "extract_gaps was never called"
    )
    assert extractor.received_covered_content == _SAMPLE_CARDS, (
        "Runner must forward build_content_cards result as covered_content"
    )
    assert run.stats["content_cards_built"] == len(_SAMPLE_CARDS)
    assert run.stats["content_cards_failed"] == 0


@pytest.mark.asyncio
async def test_card_builder_raises_run_ok_extractor_gets_none(
    db_session: AsyncSession, monkeypatch
):
    """build_content_cards raising: run stays ok, extractor sees None, stats updated."""
    await _seed_curriculum(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(2)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    async def _fake_build_content_cards_raises(session, curriculum):
        raise RuntimeError("simulated card build failure")

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr(
        "app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item
    )
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)
    monkeypatch.setattr(
        "app.freshness_pipeline.runner.build_content_cards",
        _fake_build_content_cards_raises,
    )

    extractor = CapturingExtractor([_make_finding("Graph RAG")])

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok", "Run must not fail when card builder raises"
    assert extractor.received_covered_content is None, (
        "Extractor must receive covered_content=None when card builder fails"
    )
    assert run.stats["content_cards_failed"] == 1
    assert run.stats["content_cards_built"] == 0
