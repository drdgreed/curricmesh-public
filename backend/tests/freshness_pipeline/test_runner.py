"""Tests for app.freshness_pipeline.runner.

All AI / HTTP interaction is mocked. The test session is threaded into run_org
via a minimal _make_factory shim so that run_org's commits are visible to the
test on the same db_session connection.

Test cases
----------
1. normal_run — creates SotaSources + CCRs + enrichments + advances seen + PipelineRun ok
2. second_run_zero_new — all signals already seen; step 5 skipped, no new CCRs
3. failure — extractor raises → PipelineRun failed, seen NOT advanced, exception re-raised
4. dedup_guard — existing draft [AI] CCR → finding skipped, ccrs_skipped_dup counted
5. dry_run — zero DB rows written anywhere; returned run has dry_run=True in stats
6. monitor_outcome — judge returns monitor → no CCR, GapAssessment row exists
7. judge_failure — judge raises → run failed, seen NOT advanced (mirror test 3)
8. stats_keys_all_present — every stats key (incl. zeros) is in stats on a normal run
9. generation_kill_switch_off — generator (raising if called) never invoked (safety proof)
10. generation_switch_on_success — kill switch ON, fake generator → CCR gets change_set, stats reflect
11. generation_switch_on_generator_raises — kill switch ON, generator raises → run ok, changesets_failed==1
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import GapFinding, NetBenefitAssessment
from app.freshness_pipeline import PipelineSignal
from app.freshness_pipeline.runner import run_org
from app.models.content_model import CurriculumVersion
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.freshness_pipeline import GapAssessment, PipelineSeen, PipelineRun, SourceWatchItem
from app.models.sota import SotaSource
from app.models.structure import Module
from app.models.sync import SyncLog, SyncTarget
from app.models.version import Version
from app.models.workflow import ChangeRequest
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_content_cards import (
    _make_content_version,
    _make_curriculum_version,
    _make_lineage,
    _make_member,
)


# ---------------------------------------------------------------------------
# Session factory shim
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_factory(session: AsyncSession):
    """Yield the provided session as-is; do not tear it down (conftest owns it)."""
    yield session


# ---------------------------------------------------------------------------
# Fake adapters
# ---------------------------------------------------------------------------


def _industry_signal(n: int) -> PipelineSignal:
    return PipelineSignal(
        id=f"industry-{n}",
        source_kind="industry_news",
        source="test-source",
        title=f"Industry signal {n}",
        url=f"https://example.com/{n}",
        detail=f"Detail for industry signal {n}",
        captured_at="2026-07-05T00:00:00Z",
    )


def _uni_signal(n: int) -> PipelineSignal:
    return PipelineSignal(
        id=f"uni-{n}",
        source_kind="university_syllabus",
        source="Test University",
        title=f"New topic in Test Course: Topic {n}",
        url="https://university.example.com/course",
        detail=f"New topic: Topic {n}\nCourse: Test Course (Test University)\nConfidence: high (fetch: fetched)",
        captured_at="2026-07-05T00:00:00Z",
    )


def _make_finding(topic: str, bump: str = "patch") -> GapFinding:
    return GapFinding(
        topic=topic,
        coverage_status="missing",
        evidence=["test evidence"],
        proposed_bump=bump,
        rationale=f"Test rationale for {topic}",
    )


class FakeGapExtractor:
    """Returns a fixed list of findings; records call count."""

    def __init__(self, findings: list[GapFinding]) -> None:
        self._findings = findings
        self.call_count = 0

    async def extract_gaps(
        self, covered_topics, corpus_docs, covered_content=None
    ) -> list[GapFinding]:
        self.call_count += 1
        return self._findings


class FakeFailingExtractor:
    """Raises on extract_gaps — simulates a detection failure."""

    async def extract_gaps(
        self, covered_topics, corpus_docs, covered_content=None
    ) -> list[GapFinding]:
        raise RuntimeError("simulated detection failure")


class FakeAdoptAllJudge:
    """Returns a canned adopt_now@0.9 for every finding (Phase-1-preservation fake)."""

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
            rationale="Canned adopt verdict for tests.",
        )


class FakeMonitorJudge:
    """Returns a canned monitor verdict for every finding."""

    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment:
        return NetBenefitAssessment(
            evidence_strength=0.3,
            demand_signal=0.3,
            learner_value=0.3,
            curriculum_fit=0.3,
            effort_cost=0.3,
            urgency=0.3,
            competitive_signal=0.3,
            recommendation="monitor",
            confidence=0.3,
            rationale="Insufficient evidence to adopt; monitoring.",
        )


class FakeFailingJudge:
    """Raises on judge_gap — simulates a judge-call failure."""

    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment:
        raise RuntimeError("simulated judge failure")


class FakeRaisingGenerator:
    """Raises AssertionError if called — proves the kill switch prevents invocation."""

    async def generate_asset_content(self, **kwargs) -> None:
        raise AssertionError("generator must not be called when FRESHNESS_GENERATION_ENABLED is False")


# ---------------------------------------------------------------------------
# Seed helpers (mirrors test_gap_research.py)
# ---------------------------------------------------------------------------


async def _seed_curriculum(
    session: AsyncSession,
    module_focuses: list[str] | None = None,
) -> tuple[Curriculum, Version]:
    module_focuses = module_focuses or ["Python Fundamentals", "REST APIs"]

    cur = Curriculum(name="Test Curriculum", slug=f"test-{uuid.uuid4().hex[:8]}")
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

    for i, focus in enumerate(module_focuses):
        session.add(Module(version_id=ver.id, index=i, focus=focus))
    await session.flush()

    cur.current_version_id = ver.id
    session.add(cur)
    await session.flush()

    return cur, ver


async def _seed_watch_item(session: AsyncSession) -> SourceWatchItem:
    item = SourceWatchItem(
        label="Test Course",
        institution="Test University",
        url="https://university.example.com/course",
        active=True,
    )
    session.add(item)
    await session.flush()
    return item


async def _mark_not_first_run(session: AsyncSession) -> None:
    """Insert a ledger marker so the runner's first-run seeding doesn't fire.

    The runner treats an org with ZERO PipelineSeen rows as a first run and
    seeds the entire backlog silently (no processing). Tests that exercise
    the processing path simulate an established ledger with one prior row —
    the same pattern career-foundry's orchestrator tests used.
    """
    session.add(PipelineSeen(signal_id="prior-run-marker"))
    await session.flush()


# ---------------------------------------------------------------------------
# Test 1: normal run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_run_creates_rows(db_session: AsyncSession, monkeypatch):
    """A fresh run: industry + uni signals → SotaSources + CCRs + seen + run row."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    ind_sig1, ind_sig2 = _industry_signal(1), _industry_signal(2)
    uni_sig = _uni_signal(1)

    async def _fake_fetch_all(*_, **__):
        return [ind_sig1, ind_sig2]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return [uni_sig]

    enrich_calls: list[uuid.UUID] = []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        enrich_calls.append(ccr_id)

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([
        _make_finding("MCP Integration"),
        _make_finding("Agent Observability"),
    ])

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
    stats = run.stats
    assert stats["signals_fetched"] == 3
    assert stats["new_signals"] == 3
    assert stats["ccrs_created"] == 2
    assert stats["ccrs_skipped_dup"] == 0
    assert stats["errors"] == []

    # 3 SotaSource rows (one per new signal).
    sources_count = (await db_session.execute(select(func.count()).select_from(SotaSource))).scalar_one()
    assert sources_count == 3

    # 2 draft CCRs created (adopted by the judge).
    ccrs = (await db_session.execute(select(ChangeRequest))).scalars().all()
    assert len(ccrs) == 2
    titles = {c.title for c in ccrs}
    assert "[AI] MCP Integration" in titles
    assert "[AI] Agent Observability" in titles
    assert all(c.status == LifecycleStatus.draft for c in ccrs)

    # enrich_ccr called once per CCR.
    assert len(enrich_calls) == 2

    # All 3 signals marked seen (+1 for the _mark_not_first_run ledger marker).
    seen_count = (await db_session.execute(select(func.count()).select_from(PipelineSeen))).scalar_one()
    assert seen_count == 4

    # SotaFinding rows still written per adopted gap (Phase-1 preservation, T5 review).
    from app.models.sota import SotaFinding
    sf_count = (await db_session.execute(select(func.count()).select_from(SotaFinding))).scalar_one()
    assert sf_count == 2

    # Exactly 1 PipelineRun row.
    runs = (await db_session.execute(select(PipelineRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "ok"
    assert runs[0].finished_at is not None

    assert extractor.call_count == 1


# ---------------------------------------------------------------------------
# Test 6: org_scoped_session — GUC set initially and re-issued after commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_scoped_session_guc_set_and_reaffirmed_after_commit(
    db_session: AsyncSession,
):
    """org_scoped_session pushes app.current_org GUC and re-issues it after commit.

    This proves the after_begin listener fires even under a superuser test-DB
    role — i.e. the mechanism is wired, not just accidentally passing because
    the superuser bypasses RLS.  Under production (non-superuser FORCE RLS)
    this GUC is what makes every tenant-scoped query return rows.

    db_session is included to ensure the DB is set up; the assertion runs on a
    separate session opened by org_scoped_session itself (using AsyncSessionLocal).
    """
    from sqlalchemy import text as sa_text

    from app.database import engine as app_engine
    from app.database import org_scoped_session

    # org_scoped_session uses the app's GLOBAL engine. Under the full suite,
    # earlier tests may have left pooled asyncpg connections bound to a dead
    # event loop ("attached to a different loop"). Dispose the pool so this
    # test's loop gets fresh connections — the repo-standard pattern (see
    # tests/conftest.py, tests/course/test_course_view.py).
    await app_engine.dispose()
    try:
        async with org_scoped_session(DEFAULT_ORG_ID) as session:
            # 1. GUC must be set immediately (initial _SET_LOCAL_ORG execute).
            row = await session.execute(
                sa_text("SELECT current_setting('app.current_org', true)")
            )
            assert row.scalar_one() == str(DEFAULT_ORG_ID), (
                "org_scoped_session did not set app.current_org on session open"
            )

            # 2. Commit ends the current transaction; SQLAlchemy begins a new one
            #    lazily on the next query.  The after_begin listener must re-issue
            #    the GUC into that new transaction before our query executes.
            await session.commit()

            row = await session.execute(
                sa_text("SELECT current_setting('app.current_org', true)")
            )
            assert row.scalar_one() == str(DEFAULT_ORG_ID), (
                "app.current_org GUC was lost after commit — after_begin listener "
                "did not re-issue the GUC in the new transaction"
            )
    finally:
        # Don't leave THIS loop's pooled connections behind to poison later tests.
        await app_engine.dispose()


# ---------------------------------------------------------------------------
# Test 2: second run → zero new signals, zero duplicate CCRs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_run_no_new_signals_no_dup_ccrs(db_session: AsyncSession, monkeypatch):
    """Immediate second run: signals already seen, findings match existing draft CCRs."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    ind_sig = _industry_signal(1)

    async def _fake_fetch_all(*_, **__):
        return [ind_sig]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([
        _make_finding("MCP Integration"),
        _make_finding("Agent Observability"),
    ])

    # First run — seeds SotaSources, CCRs, and seen state.
    run1 = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )
    assert run1.status == "ok"
    assert run1.stats["new_signals"] == 1
    assert run1.stats["ccrs_created"] == 2

    # Second run — nothing new.
    run2 = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )
    assert run2.status == "ok"
    assert run2.stats["new_signals"] == 0
    assert run2.stats["ccrs_created"] == 0
    # With zero new signals the corpus is unchanged, so detection is SKIPPED
    # entirely (no API cost) rather than re-run-and-deduped (T7 review).
    assert run2.stats["ccrs_skipped_dup"] == 0
    assert extractor.call_count == 1  # run 2 never called the model

    # Total CCRs unchanged.
    ccr_count = (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one()
    assert ccr_count == 2

    # Two PipelineRun rows total (one per run).
    run_count = (await db_session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()
    assert run_count == 2


# ---------------------------------------------------------------------------
# Test 3: detection failure → PipelineRun failed, seen NOT advanced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_records_run_and_does_not_advance_seen(db_session: AsyncSession, monkeypatch):
    """An exception in extract_gaps: PipelineRun=failed, no PipelineSeen rows, re-raised."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1), _industry_signal(2)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)

    # FakeFailingExtractor raises on extract_gaps — simulates a detection failure.
    extractor = FakeFailingExtractor()

    with pytest.raises(RuntimeError, match="simulated detection failure"):
        await run_org(
            lambda: _make_factory(db_session),
            DEFAULT_ORG_ID,
            extractor=extractor,
            searcher=None,
            enricher=None,
            judge=FakeAdoptAllJudge(),
            generator=FakeRaisingGenerator(),
        )

    # Failure run row recorded.
    runs = (await db_session.execute(select(PipelineRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    # Seen-state NOT advanced.
    seen_count = (await db_session.execute(select(func.count()).select_from(PipelineSeen))).scalar_one()
    assert seen_count == 0


# ---------------------------------------------------------------------------
# Test 4: dedup guard — existing draft [AI] CCR skips the duplicate finding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_guard_skips_existing_draft_ccr(db_session: AsyncSession, monkeypatch):
    """Finding whose topic matches an existing draft [AI] CCR is skipped."""
    cur, _ = await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    # Pre-seed a draft [AI] CCR for the curriculum.
    existing_ccr = ChangeRequest(
        curriculum_id=cur.id,
        title="[AI] MCP Integration",
        status=LifecycleStatus.draft,
        proposed_bump="patch",
        rationale="pre-existing",
    )
    db_session.add(existing_ccr)
    await db_session.flush()

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([_make_finding("MCP Integration")])

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
    assert run.stats["ccrs_skipped_dup"] == 1
    assert run.stats["ccrs_created"] == 0

    # Only the pre-seeded CCR exists — no new CCR was created.
    ccr_count = (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one()
    assert ccr_count == 1


# ---------------------------------------------------------------------------
# Test 5: dry_run → zero rows written anywhere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(db_session: AsyncSession, monkeypatch):
    """dry_run=True: no SotaSource, CCR, PipelineSeen, or PipelineRun rows in DB."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    ind_sig = _industry_signal(1)

    async def _fake_fetch_all(*_, **__):
        return [ind_sig]

    seen_dry_run = {}

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        # Capture the kwarg so the test proves dry_run genuinely reached the adapter.
        seen_dry_run["value"] = dry_run
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)

    extractor = FakeGapExtractor([_make_finding("MCP Integration"), _make_finding("Agent Observability")])

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
        dry_run=True,
    )

    # The dry_run kwarg genuinely reached the university adapter (T7 review).
    assert seen_dry_run["value"] is True
    # Stats reflect what WOULD have happened.
    assert run.stats["dry_run"] is True
    assert run.stats["signals_fetched"] == 1
    assert run.stats["new_signals"] == 1
    assert run.stats["ccrs_created"] == 0  # analyze_gaps skipped

    # Zero rows written (PipelineSeen keeps ONLY the pre-seeded _mark_not_first_run marker).
    assert (await db_session.execute(select(func.count()).select_from(SotaSource))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(PipelineSeen))).scalar_one() == 1
    assert (await db_session.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 0


# ---------------------------------------------------------------------------
# First-run seeding + per-run cap (added after the 2026-07-05 dry-run exposed
# the 1,258-signal industry backlog problem)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_run_seeds_backlog_silently(db_session: AsyncSession, monkeypatch):
    """Org with an EMPTY seen-ledger: all signals seeded as seen, nothing processed."""
    await _seed_curriculum(db_session)

    sigs = [_industry_signal(i) for i in range(5)]

    async def _fake_fetch_all(*_, **__):
        return sigs

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)

    extractor = FakeGapExtractor([_make_finding("Should Never Appear")])
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
    assert run.stats["first_run_seeded"] == 5
    assert run.stats["new_signals"] == 0
    assert run.stats["ccrs_created"] == 0
    assert extractor.call_count == 0  # detection never ran
    # All 5 seeded as seen; zero SotaSources / CCRs.
    assert (await db_session.execute(select(func.count()).select_from(PipelineSeen))).scalar_one() == 5
    assert (await db_session.execute(select(func.count()).select_from(SotaSource))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one() == 0


@pytest.mark.asyncio
async def test_per_run_cap_defers_surplus(db_session: AsyncSession, monkeypatch):
    """More new signals than MAX_SIGNALS_PER_RUN: batch capped, surplus NOT marked seen."""
    from app.freshness_pipeline.runner import MAX_SIGNALS_PER_RUN

    await _seed_curriculum(db_session)
    await _mark_not_first_run(db_session)

    total = MAX_SIGNALS_PER_RUN + 7
    sigs = [_industry_signal(i) for i in range(total)]

    async def _fake_fetch_all(*_, **__):
        return sigs

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([])  # no findings; we only care about the cap
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
    assert run.stats["new_signals"] == MAX_SIGNALS_PER_RUN
    assert run.stats["signals_deferred"] == 7
    # Only the capped batch became SotaSources and was marked seen;
    # the 7 deferred signals remain unseen and drain on the next run.
    assert (await db_session.execute(select(func.count()).select_from(SotaSource))).scalar_one() == MAX_SIGNALS_PER_RUN
    seen = (await db_session.execute(select(PipelineSeen.signal_id))).scalars().all()
    assert len([s for s in seen if s.startswith("industry-")]) == MAX_SIGNALS_PER_RUN


# ---------------------------------------------------------------------------
# New Phase-2 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_outcome_no_ccr_and_assessment_row(db_session: AsyncSession, monkeypatch):
    """Judge returns monitor → no CCR created, GapAssessment row persisted, stats updated."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([_make_finding("LLM Observability")])

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeMonitorJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok"
    assert run.stats["gaps_judged"] == 1
    assert run.stats["gaps_monitored"] == 1
    assert run.stats["ccrs_created"] == 0

    # No CCR was created.
    ccr_count = (await db_session.execute(select(func.count()).select_from(ChangeRequest))).scalar_one()
    assert ccr_count == 0

    # One GapAssessment row was created with recommendation=monitor.
    assessments = (await db_session.execute(select(GapAssessment))).scalars().all()
    assert len(assessments) == 1
    assert assessments[0].recommendation == "monitor"
    assert assessments[0].topic == "llm observability"  # lowercase identity


@pytest.mark.asyncio
async def test_judge_failure_fails_run_and_does_not_advance_seen(db_session: AsyncSession, monkeypatch):
    """A judge-call failure: PipelineRun=failed, no PipelineSeen rows advanced, re-raised."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1), _industry_signal(2)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)

    extractor = FakeGapExtractor([_make_finding("Agent Observability")])

    with pytest.raises(RuntimeError, match="simulated judge failure"):
        await run_org(
            lambda: _make_factory(db_session),
            DEFAULT_ORG_ID,
            extractor=extractor,
            searcher=None,
            enricher=None,
            judge=FakeFailingJudge(),
            generator=FakeRaisingGenerator(),
        )

    # Failure PipelineRun row recorded.
    runs = (await db_session.execute(select(PipelineRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "failed"

    # Seen-state NOT advanced (the _mark_not_first_run marker was rolled back too).
    seen_count = (await db_session.execute(select(func.count()).select_from(PipelineSeen))).scalar_one()
    assert seen_count == 0


@pytest.mark.asyncio
async def test_stats_keys_all_present_with_zeros(db_session: AsyncSession, monkeypatch):
    """All stats keys (including Phase-2 gap counters) are present and zero on a run with no findings."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    # No findings → all gap counters stay zero; still tests that the keys exist.
    extractor = FakeGapExtractor([])

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
    required_keys = {
        "dry_run", "signals_fetched", "new_signals", "ccrs_created",
        "ccrs_skipped_dup", "search_only_items", "gaps_judged", "gaps_adopted",
        "gaps_monitored", "gaps_rejected", "gaps_resurrected", "gaps_strengthened",
        "errors",
    }
    assert required_keys.issubset(set(run.stats.keys()))
    # All Phase-2 gap counters are zero when the extractor returns no findings.
    for key in ("gaps_judged", "gaps_adopted", "gaps_monitored", "gaps_rejected",
                "gaps_resurrected", "gaps_strengthened"):
        assert run.stats[key] == 0, f"Expected {key}==0, got {run.stats[key]}"


@pytest.mark.asyncio
async def test_skipped_promoted_topic_not_counted_as_judged(db_session: AsyncSession, monkeypatch):
    """A re-appearing already-promoted topic is routed but NOT judged — stats must
    keep judged == adopted+monitored+rejected (T5 review)."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    ind_sig1 = _industry_signal(1)

    async def _fake_fetch_all(*_, **__):
        return [ind_sig1]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)

    extractor = FakeGapExtractor([_make_finding("Repeat Topic")])
    judge = FakeAdoptAllJudge()

    run1 = await run_org(
        lambda: _make_factory(db_session), DEFAULT_ORG_ID,
        extractor=extractor, searcher=None, enricher=None, judge=judge,
        generator=FakeRaisingGenerator(),
    )
    assert run1.stats["gaps_judged"] == 1
    assert run1.stats["gaps_adopted"] == 1

    # Second run: new signal id so the seen-filter passes, same topic. Move the
    # CCR out of draft (approved) so the Phase-1 title guard (drafts only) does
    # NOT fire — exposing the judge's own already-promoted skip path (Case 4).
    from app.models.enums import LifecycleStatus
    from app.models.workflow import ChangeRequest
    ccr_row = (await db_session.execute(select(ChangeRequest))).scalar_one()
    ccr_row.status = LifecycleStatus.approved
    db_session.add(ccr_row)
    await db_session.flush()

    ind_sig2 = _industry_signal(2)

    async def _fake_fetch_all2(*_, **__):
        return [ind_sig2]

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all2)

    run2 = await run_org(
        lambda: _make_factory(db_session), DEFAULT_ORG_ID,
        extractor=extractor, searcher=None, enricher=None, judge=judge,
        generator=FakeRaisingGenerator(),
    )
    # Routed but NOT judged: arithmetic stays consistent.
    assert run2.stats["gaps_judged"] == 0
    assert run2.stats["gaps_adopted"] == 0
    assert run2.stats["ccrs_created"] == 0
    assert (
        run2.stats["gaps_judged"]
        == run2.stats["gaps_adopted"] + run2.stats["gaps_monitored"] + run2.stats["gaps_rejected"]
    )


# ---------------------------------------------------------------------------
# Phase-3 generation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_kill_switch_off_generator_never_called(
    db_session: AsyncSession, monkeypatch
):
    """Kill switch OFF (default): FakeRaisingGenerator must not be invoked on an
    adopted run — the safety proof for the July-15 prod run."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)
    # Kill switch stays at its default (False) — no monkeypatch needed.

    extractor = FakeGapExtractor([_make_finding("MCP Integration")])

    # FakeRaisingGenerator raises AssertionError if called; the run must succeed.
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
    assert run.stats["ccrs_created"] == 1
    # Generation stats are zero — kill switch was OFF.
    assert run.stats["changesets_generated"] == 0
    assert run.stats["changesets_failed"] == 0


@pytest.mark.asyncio
async def test_generation_switch_on_adopted_ccr_gets_change_set(
    db_session: AsyncSession, monkeypatch
):
    """Kill switch ON: adopted CCR receives a valid ReleaseChangeSet.

    The fake enricher writes a minimal valid enrichment (modify_asset → seeded
    lineage_key) so generate_change_set can proceed.  Immutable model rows are
    seeded with the test_content_cards helpers (same pattern as test_generation.py).
    """
    from app.models.enums import AssetKind
    from app.schemas.release import ReleaseChangeSet
    from tests.freshness_pipeline.test_content_cards import (
        _make_content_version,
        _make_curriculum_version,
        _make_lineage,
        _make_member,
    )
    from tests.freshness_pipeline.test_generation import FakeContentGenerator

    # 1. Seed legacy curriculum (needed by _resolve_version for detection).
    cur, _ = await _seed_curriculum(db_session, module_focuses=["Python Fundamentals"])
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    # 2. Seed immutable model rows on the same curriculum (needed by generation).
    cv_ver = await _make_curriculum_version(db_session, curriculum_id=cur.id)
    asset = await _make_lineage(db_session, key="wk01/lesson_plan", kind=AssetKind.lesson_plan)
    body = "# Week 1\n\n## Core Concepts\nOriginal content."
    content = await _make_content_version(db_session, asset=asset, seq=1, body=body)
    await _make_member(
        db_session,
        curriculum_version_id=cv_ver.id,
        asset=asset,
        content_version=content,
        section="Week 1",
        week_index=1,
        order=0,
    )
    cur.active_content_version_id = cv_ver.id
    await db_session.flush()

    # 3. Fake enricher writes a valid enrichment targeting the seeded member.
    #    Because enrich_ccr receives ccr_id (not the object), we load it via the
    #    session — SQLAlchemy's identity map returns the same in-memory object,
    #    so the runner's adopted_ccrs reference sees the updated impact.
    async def _enriching_fake_enrich_ccr(session, *, ccr_id, enricher):
        ccr_obj = (
            await session.execute(select(ChangeRequest).where(ChangeRequest.id == ccr_id))
        ).scalar_one()
        new_impact = dict(ccr_obj.impact or {})
        new_impact["enrichment"] = {
            "placement": {
                "target_kind": "modify_asset",
                "target_ref": "wk01/lesson_plan",
                "position_hint": "replace existing lesson",
                "rationale": "gap evidence",
                "confidence": 0.9,
            },
            "draft_frame": {
                "outline": ["Point A", "Point B"],
                "sample_assessments": [],
                "caveats": [],
            },
        }
        ccr_obj.impact = new_impact
        session.add(ccr_obj)
        await session.flush()

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _enriching_fake_enrich_ccr)
    monkeypatch.setattr(
        "app.freshness_pipeline.runner.settings.FRESHNESS_GENERATION_ENABLED", True
    )

    extractor = FakeGapExtractor([_make_finding("MCP Integration")])
    generator = FakeContentGenerator()

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=extractor,
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=generator,
    )

    assert run.status == "ok"
    assert run.stats["ccrs_created"] == 1
    assert run.stats["changesets_generated"] == 1
    assert run.stats["changesets_failed"] == 0

    # The CCR must carry a valid ReleaseChangeSet.
    ccr_rows = (await db_session.execute(select(ChangeRequest))).scalars().all()
    assert len(ccr_rows) == 1
    ccr = ccr_rows[0]
    assert ccr.change_set is not None
    validated = ReleaseChangeSet.model_validate(ccr.change_set)
    assert len(validated.changed) == 1
    assert validated.changed[0].lineage_key == "wk01/lesson_plan"


@pytest.mark.asyncio
async def test_generation_switch_on_generator_raises_run_ok(
    db_session: AsyncSession, monkeypatch
):
    """Kill switch ON + generator raises → run completes ok, changesets_failed==1,
    CCR keeps its Phase-2 shape (change_set is None)."""
    await _seed_curriculum(db_session)
    await _seed_watch_item(db_session)
    await _mark_not_first_run(db_session)

    async def _fake_fetch_all(*_, **__):
        return [_industry_signal(1)]

    async def _fake_check_watch_item(session, item, *, extractor, searcher, http, dry_run=False):
        return []

    async def _fake_enrich_ccr(session, *, ccr_id, enricher):
        pass

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _fake_fetch_all)
    monkeypatch.setattr("app.freshness_pipeline.university.check_watch_item", _fake_check_watch_item)
    monkeypatch.setattr("app.freshness_pipeline.runner.enrich_ccr", _fake_enrich_ccr)
    monkeypatch.setattr(
        "app.freshness_pipeline.runner.settings.FRESHNESS_GENERATION_ENABLED", True
    )

    extractor = FakeGapExtractor([_make_finding("Agent Observability")])

    # RaisingGenerator raises when called.
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
    assert run.stats["ccrs_created"] == 1
    # Generation attempted but caught — counted as failed.
    assert run.stats["changesets_failed"] == 1
    assert run.stats["changesets_generated"] == 0

    # CCR keeps Phase-2 shape: change_set is None.
    ccr_rows = (await db_session.execute(select(ChangeRequest))).scalars().all()
    assert len(ccr_rows) == 1
    assert ccr_rows[0].change_set is None


# ---------------------------------------------------------------------------
# Phase-4 sync sweep tests
# ---------------------------------------------------------------------------


async def _seed_sync_scenario(session: AsyncSession) -> tuple[Curriculum, CurriculumVersion]:
    """Seed a Curriculum with an active CurriculumVersion (content model) and return both.

    The Curriculum's active_content_version_id is set so the sweep can find it.
    """
    cur = Curriculum(name="Sweep Test Curriculum", slug=f"sweep-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    cv = await _make_curriculum_version(session, curriculum_id=cur.id)
    asset = await _make_lineage(session, key="wk01/sweep-test", kind=AssetKind.lesson_plan)
    content = await _make_content_version(session, asset=asset, seq=1, body="Sweep test content")
    await _make_member(
        session,
        curriculum_version_id=cv.id,
        asset=asset,
        content_version=content,
        section="sweep-section",
        week_index=1,
        order=1,
    )
    cur.active_content_version_id = cv.id
    session.add(cur)
    await session.flush()
    return cur, cv


@pytest.mark.asyncio
async def test_sweep_syncs_unsynced_active_version(db_session: AsyncSession, monkeypatch):
    """Sweep: active version with no success SyncLog → sync_release called, stats updated."""
    cur, cv = await _seed_sync_scenario(db_session)

    # Add an active SyncTarget for this curriculum
    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "org/repo", "base_branch": "main", "path_prefix": "curriculum"},
        active=True,
    )
    db_session.add(target)
    await db_session.flush()

    # Fake sync_release: records calls and creates a success SyncLog
    sync_calls: list[dict] = []

    async def _fake_sync_release(session, *, curriculum, new_version, target, ccr=None):
        sync_calls.append({"cv_id": new_version.id})
        log = SyncLog(
            curriculum_id=curriculum.id,
            version_id=None,
            curriculum_version_id=new_version.id,
            target="github",
            status="success",
            detail={"url": "https://github.com/org/repo/pull/1"},
        )
        session.add(log)
        await session.flush()
        return log

    monkeypatch.setattr("app.freshness_pipeline.runner.sync_release", _fake_sync_release)
    monkeypatch.setattr("app.freshness_pipeline.runner.settings.FRESHNESS_SYNC_ENABLED", True)
    monkeypatch.setattr("app.freshness_pipeline.runner.settings.SYNC_GITHUB_TOKEN", "test-token")

    # Minimal signal patch so the runner completes without errors
    async def _no_signals(*_, **__):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _no_signals)

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=FakeGapExtractor([]),
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok"
    assert run.stats["syncs_attempted"] == 1
    assert run.stats["syncs_succeeded"] == 1
    assert run.stats["syncs_failed"] == 0
    assert len(sync_calls) == 1
    assert sync_calls[0]["cv_id"] == cv.id


@pytest.mark.asyncio
async def test_sweep_skips_already_synced_version(db_session: AsyncSession, monkeypatch):
    """Sweep: version with an existing success SyncLog → sync_release NOT called."""
    cur, cv = await _seed_sync_scenario(db_session)

    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "org/repo", "base_branch": "main", "path_prefix": "curriculum"},
        active=True,
    )
    db_session.add(target)
    await db_session.flush()

    # Seed a success SyncLog so the sweep sees it as already synced
    success_log = SyncLog(
        curriculum_id=cur.id,
        version_id=None,
        curriculum_version_id=cv.id,
        target="github",
        status="success",
        detail={"url": "https://github.com/org/repo/pull/1"},
    )
    db_session.add(success_log)
    await db_session.flush()

    sync_called = False

    async def _should_not_be_called(*_, **__):
        nonlocal sync_called
        sync_called = True

    monkeypatch.setattr("app.freshness_pipeline.runner.sync_release", _should_not_be_called)
    monkeypatch.setattr("app.freshness_pipeline.runner.settings.FRESHNESS_SYNC_ENABLED", True)
    monkeypatch.setattr("app.freshness_pipeline.runner.settings.SYNC_GITHUB_TOKEN", "test-token")

    async def _no_signals(*_, **__):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _no_signals)

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=FakeGapExtractor([]),
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok"
    assert run.stats["syncs_attempted"] == 0
    assert not sync_called, "sync_release must not be called when success SyncLog already exists"


@pytest.mark.asyncio
async def test_sweep_skipped_when_switch_off(db_session: AsyncSession, monkeypatch):
    """Sweep is entirely skipped when FRESHNESS_SYNC_ENABLED=False (default)."""
    cur, cv = await _seed_sync_scenario(db_session)

    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "org/repo", "base_branch": "main", "path_prefix": "curriculum"},
        active=True,
    )
    db_session.add(target)
    await db_session.flush()

    sync_called = False

    async def _should_not_be_called(*_, **__):
        nonlocal sync_called
        sync_called = True

    monkeypatch.setattr("app.freshness_pipeline.runner.sync_release", _should_not_be_called)
    # FRESHNESS_SYNC_ENABLED defaults to False — no monkeypatch needed; explicitly set for clarity
    monkeypatch.setattr("app.freshness_pipeline.runner.settings.FRESHNESS_SYNC_ENABLED", False)

    async def _no_signals(*_, **__):
        return []

    monkeypatch.setattr("app.freshness_pipeline.industry.fetch_all", _no_signals)

    run = await run_org(
        lambda: _make_factory(db_session),
        DEFAULT_ORG_ID,
        extractor=FakeGapExtractor([]),
        searcher=None,
        enricher=None,
        judge=FakeAdoptAllJudge(),
        generator=FakeRaisingGenerator(),
    )

    assert run.status == "ok"
    assert run.stats["syncs_attempted"] == 0
    assert not sync_called, "sync_release must not be called when FRESHNESS_SYNC_ENABLED is False"
