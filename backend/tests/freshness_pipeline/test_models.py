"""Round-trip tests for freshness-pipeline TenantScoped models + sync models.

All tests run under DEFAULT_ORG_ID tenant context, established by the
``db_session`` fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.freshness_pipeline import (
    GapAssessment,
    PipelineRun,
    PipelineSeen,
    SourceWatchItem,
    SyllabusSnapshot,
)
from app.models.sync import SyncLog, SyncTarget
from tests.conftest import DEFAULT_ORG_ID
from tests.freshness_pipeline.test_runner import _seed_curriculum


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_watch_item(session: AsyncSession) -> SourceWatchItem:
    item = SourceWatchItem(
        label="CS294 Agentic AI",
        institution="UC Berkeley",
        url="https://rdi.berkeley.edu/agentic-ai/f25",
        search_hint="Berkeley CS294 agentic AI syllabus",
    )
    session.add(item)
    await session.flush()
    return item


# ---------------------------------------------------------------------------
# Round-trip: SourceWatchItem
# ---------------------------------------------------------------------------


async def test_source_watch_item_round_trip(db_session: AsyncSession):
    """Insert a SourceWatchItem and query it back; defaults are applied."""
    item = await _make_watch_item(db_session)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(SourceWatchItem).where(SourceWatchItem.id == item.id)
        )
    ).scalar_one()

    assert row.label == "CS294 Agentic AI"
    assert row.institution == "UC Berkeley"
    assert row.url == "https://rdi.berkeley.edu/agentic-ai/f25"
    assert row.search_hint == "Berkeley CS294 agentic AI syllabus"
    assert row.active is True
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Round-trip: SyllabusSnapshot
# ---------------------------------------------------------------------------


async def test_syllabus_snapshot_round_trip(db_session: AsyncSession):
    """Insert a SyllabusSnapshot linked to a watch item; query it back."""
    item = await _make_watch_item(db_session)

    snap = SyllabusSnapshot(
        watch_item_id=item.id,
        topics={"topics": ["Agents", "RAG", "Tool Use"]},
        raw_summary="A rigorous course on agentic AI.",
        content_hash="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        confidence="fetched",
    )
    db_session.add(snap)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(SyllabusSnapshot).where(SyllabusSnapshot.id == snap.id)
        )
    ).scalar_one()

    assert row.watch_item_id == item.id
    assert row.topics == {"topics": ["Agents", "RAG", "Tool Use"]}
    assert row.raw_summary == "A rigorous course on agentic AI."
    assert row.content_hash == "abc123def456abc123def456abc123def456abc123def456abc123def456abc1"
    assert row.confidence == "fetched"
    assert row.captured_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Round-trip: PipelineSeen
# ---------------------------------------------------------------------------


async def test_pipeline_seen_round_trip(db_session: AsyncSession):
    """Insert a PipelineSeen row and query it back."""
    seen = PipelineSeen(signal_id="industry_news:openai:abc123")
    db_session.add(seen)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(PipelineSeen).where(PipelineSeen.id == seen.id)
        )
    ).scalar_one()

    assert row.signal_id == "industry_news:openai:abc123"
    assert row.seen_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Unique constraint: PipelineSeen (signal_id, organization_id)
# ---------------------------------------------------------------------------


async def test_pipeline_seen_unique_constraint(db_session: AsyncSession):
    """Duplicate (signal_id, organization_id) must raise IntegrityError (P-004 note:
    SQLAlchemy wraps the DB error but the constraint still fires)."""
    sig = f"industry_news:openai:{uuid.uuid4().hex}"

    seen1 = PipelineSeen(signal_id=sig)
    db_session.add(seen1)
    await db_session.commit()

    seen2 = PipelineSeen(signal_id=sig)
    db_session.add(seen2)
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


# ---------------------------------------------------------------------------
# Round-trip: PipelineRun
# ---------------------------------------------------------------------------


async def test_pipeline_run_round_trip(db_session: AsyncSession):
    """Insert a PipelineRun and query it back; defaults applied."""
    run = PipelineRun(stats={"signals_fetched": 10, "new_signals": 3})
    db_session.add(run)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.id == run.id)
        )
    ).scalar_one()

    assert row.status == "running"
    assert row.digest_sent is False
    assert row.finished_at is None
    assert row.stats == {"signals_fetched": 10, "new_signals": 3}
    assert row.started_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Round-trip: GapAssessment
# ---------------------------------------------------------------------------


async def test_gap_assessment_round_trip(db_session: AsyncSession):
    """Insert a GapAssessment linked to a curriculum and query it back; defaults applied."""
    cur, _ver = await _seed_curriculum(db_session)

    assessment = GapAssessment(
        curriculum_id=cur.id,
        topic="mcp integration",
        display_topic="MCP Integration",
        recommendation="monitor",
        confidence=0.6,
        scores={"evidence_strength": 0.7, "demand_signal": 0.8},
        rationale="Multiple independent sources confirm growing adoption.",
        dossier=[{"run_date": "2026-07-05", "source_kinds": ["industry_news"], "evidence": ["Source A"]}],
    )
    db_session.add(assessment)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(GapAssessment).where(GapAssessment.id == assessment.id)
        )
    ).scalar_one()

    assert row.curriculum_id == cur.id
    assert row.topic == "mcp integration"
    assert row.display_topic == "MCP Integration"
    assert row.recommendation == "monitor"
    assert row.confidence == 0.6
    assert row.scores["evidence_strength"] == 0.7
    assert row.rationale == "Multiple independent sources confirm growing adoption."
    assert len(row.dossier) == 1
    assert row.dossier[0]["run_date"] == "2026-07-05"
    assert row.times_seen == 1
    assert row.times_seen_at_last_eval == 1
    assert row.promoted_ccr_id is None
    assert row.first_seen_at is not None
    assert row.last_evaluated_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Unique constraint: GapAssessment (curriculum_id, topic, organization_id)
# ---------------------------------------------------------------------------


async def test_gap_assessment_unique_constraint(db_session: AsyncSession):
    """Duplicate (curriculum_id, topic, organization_id) must raise IntegrityError."""
    cur, _ver = await _seed_curriculum(db_session)
    topic = f"agent observability {uuid.uuid4().hex}"

    assessment1 = GapAssessment(
        curriculum_id=cur.id,
        topic=topic,
        display_topic="Agent Observability",
        recommendation="monitor",
        confidence=0.55,
        scores={"evidence_strength": 0.6},
        rationale="First sighting.",
        dossier=[],
    )
    db_session.add(assessment1)
    await db_session.commit()

    assessment2 = GapAssessment(
        curriculum_id=cur.id,
        topic=topic,
        display_topic="Agent Observability",
        recommendation="reject",
        confidence=0.2,
        scores={"evidence_strength": 0.1},
        rationale="Duplicate entry.",
        dossier=[],
    )
    db_session.add(assessment2)
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


# ---------------------------------------------------------------------------
# Round-trip: SyncTarget (Phase 4)
# ---------------------------------------------------------------------------


async def test_sync_target_round_trip(db_session: AsyncSession):
    """Insert a SyncTarget and query it back; defaults are applied."""
    cur, _ver = await _seed_curriculum(db_session)

    target = SyncTarget(
        curriculum_id=cur.id,
        kind="github_pr",
        config={"repo": "my-org/my-curriculum", "base_branch": "main", "path_prefix": "content/"},
        active=True,
    )
    db_session.add(target)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(SyncTarget).where(SyncTarget.id == target.id)
        )
    ).scalar_one()

    assert row.curriculum_id == cur.id
    assert row.kind == "github_pr"
    assert row.config["repo"] == "my-org/my-curriculum"
    assert row.config["base_branch"] == "main"
    assert row.config["path_prefix"] == "content/"
    assert row.active is True
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Round-trip: SyncLog with curriculum_version_id (new-model path, no version_id)
# ---------------------------------------------------------------------------


async def test_sync_log_with_curriculum_version_id(db_session: AsyncSession):
    """SyncLog can be inserted with version_id=None and curriculum_version_id set.

    This proves that relaxing version_id to nullable works for Phase-4
    new-model releases that have no legacy Version row.
    """
    cur, _ver = await _seed_curriculum(db_session)

    log = SyncLog(
        curriculum_id=cur.id,
        version_id=None,              # no legacy version — relaxed nullable
        curriculum_version_id=None,   # no CV in this test DB (no full immutable stack)
        target="github",
        status="success",
        detail={"url": "https://github.com/my-org/curriculum/pull/1", "message": "ok"},
    )
    db_session.add(log)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(SyncLog).where(SyncLog.id == log.id)
        )
    ).scalar_one()

    assert row.curriculum_id == cur.id
    assert row.version_id is None
    assert row.curriculum_version_id is None
    assert row.target == "github"
    assert row.status == "success"
    assert row.detail["url"] == "https://github.com/my-org/curriculum/pull/1"
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


async def test_sync_log_legacy_version_id_still_works(db_session: AsyncSession):
    """Existing callers that pass version_id still work after the nullable relaxation."""
    cur, ver = await _seed_curriculum(db_session)

    log = SyncLog(
        curriculum_id=cur.id,
        version_id=ver.id,             # legacy path — still valid
        curriculum_version_id=None,
        target="lms",
        status="failed",
        detail={"error": "timeout"},
    )
    db_session.add(log)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(SyncLog).where(SyncLog.id == log.id)
        )
    ).scalar_one()

    assert row.version_id == ver.id
    assert row.curriculum_version_id is None
    assert row.status == "failed"


# ---------------------------------------------------------------------------
# Unit test: build_lesson_source_url_map mapping logic (pure, no DB)
# ---------------------------------------------------------------------------


def test_build_lesson_source_url_map_correctness():
    """build_lesson_source_url_map returns correct {lineage_key: file} pairs."""
    from seed.load_agentic_mastery import SLUG, build_lesson_source_url_map

    curriculum_json = {
        "milestones": [
            {
                "id": "m0",
                "modules": [
                    {"slug": "m0-1", "number": "0.1", "file": "M0.1-llm.md"},
                    {"slug": "m0-2", "number": "0.2", "file": "M0.2-sdk.md"},
                ],
            },
            {
                "id": "m1",
                "modules": [
                    {"slug": "m1-1", "number": "1.1", "file": "M1.1-prompting.md"},
                ],
            },
        ]
    }
    mapping = build_lesson_source_url_map(curriculum_json)

    assert len(mapping) == 3
    assert mapping[f"{SLUG}/v1/01/lesson_plan"] == "M0.1-llm.md"
    assert mapping[f"{SLUG}/v1/02/lesson_plan"] == "M0.2-sdk.md"
    assert mapping[f"{SLUG}/v1/03/lesson_plan"] == "M1.1-prompting.md"
    # Keys must be lesson_plan only (no assessment / rubric).
    for key in mapping:
        assert key.endswith("/lesson_plan")
