import json

import pytest
from app.ai.client import AIClient
from app.ai.schemas import GapFinding, NetBenefitAssessment


def _canned_assessment() -> NetBenefitAssessment:
    return NetBenefitAssessment(
        evidence_strength=0.8,
        demand_signal=0.75,
        learner_value=0.9,
        curriculum_fit=0.7,
        effort_cost=0.6,
        urgency=0.65,
        competitive_signal=0.5,
        recommendation="adopt_now",
        confidence=0.85,
        rationale=(
            "Strong multi-source evidence from job postings and vendor docs. "
            "The skill integrates naturally into existing modules. "
            "High learner-employability uplift with moderate implementation effort."
        ),
    )


def _sample_finding() -> GapFinding:
    return GapFinding(
        topic="model context protocol",
        coverage_status="missing",
        evidence=["Anthropic docs describe MCP as the standard tool-use protocol"],
        proposed_bump="minor",
        rationale="MCP is increasingly required in agentic AI roles.",
    )


@pytest.mark.asyncio
async def test_judge_gap_calls_parse_with_correct_output_format(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["system"] = system
        captured["output_format"] = output_format
        captured["user"] = user
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)
    finding = _sample_finding()
    covered_topics = ["llm fundamentals", "tool use"]
    dossier = [{"run_date": "2026-07-01", "evidence": ["MCP adoption in 40% of agentic job postings"]}]

    await client.judge_gap(finding=finding, covered_topics=covered_topics, dossier=dossier)

    assert captured["output_format"] is NetBenefitAssessment
    # The GOVERNED judge prompt must actually be the one sent (T2 review).
    assert "curriculum investment judge" in captured["system"]


@pytest.mark.asyncio
async def test_judge_gap_includes_finding_topic_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)
    finding = _sample_finding()
    covered_topics = ["llm fundamentals", "tool use"]
    dossier = [{"run_date": "2026-07-01", "evidence": ["MCP adoption in 40% of agentic job postings"]}]

    await client.judge_gap(finding=finding, covered_topics=covered_topics, dossier=dossier)

    assert finding.topic in captured["user"]


@pytest.mark.asyncio
async def test_judge_gap_includes_covered_topic_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)
    finding = _sample_finding()
    covered_topics = ["llm fundamentals", "tool use"]
    dossier = [{"run_date": "2026-07-01", "evidence": ["MCP adoption in 40% of agentic job postings"]}]

    await client.judge_gap(finding=finding, covered_topics=covered_topics, dossier=dossier)

    # At least one covered topic must appear in the user prompt
    assert covered_topics[0] in captured["user"]


@pytest.mark.asyncio
async def test_judge_gap_includes_dossier_evidence_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_assessment()

    monkeypatch.setattr(client, "_parse", fake_parse)
    finding = _sample_finding()
    covered_topics = ["llm fundamentals", "tool use"]
    dossier_evidence = "MCP adoption in 40% of agentic job postings"
    dossier = [{"run_date": "2026-07-01", "evidence": [dossier_evidence]}]

    await client.judge_gap(finding=finding, covered_topics=covered_topics, dossier=dossier)

    # The dossier is serialized as JSON — the evidence string must appear
    assert dossier_evidence in captured["user"]


@pytest.mark.asyncio
async def test_judge_gap_returns_object_round_trips(monkeypatch):
    client = AIClient(api_key="test")
    canned = _canned_assessment()

    async def fake_parse(*, system, user, output_format):
        return canned

    monkeypatch.setattr(client, "_parse", fake_parse)
    finding = _sample_finding()
    covered_topics = ["llm fundamentals"]
    dossier = [{"run_date": "2026-07-01", "evidence": ["some evidence"]}]

    result = await client.judge_gap(
        finding=finding, covered_topics=covered_topics, dossier=dossier
    )

    assert result.recommendation == canned.recommendation
    assert result.confidence == canned.confidence
    assert result.evidence_strength == canned.evidence_strength
    assert result.demand_signal == canned.demand_signal
    assert result.learner_value == canned.learner_value
    assert result.curriculum_fit == canned.curriculum_fit
    assert result.effort_cost == canned.effort_cost
    assert result.urgency == canned.urgency
    assert result.competitive_signal == canned.competitive_signal
    assert result.rationale == canned.rationale
