import pytest
from app.ai.client import AIClient
from app.ai.schemas import (
    CurriculumStructure, StructureModule, DraftFrame, GapFinding, Placement, SampleAssessment,
)


def _finding() -> GapFinding:
    return GapFinding(topic="eval/observability", coverage_status="missing",
                      evidence=["job posting X"], proposed_bump="minor", rationale="employers expect it")


@pytest.mark.asyncio
async def test_place_gap_calls_parse_with_placement_format(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["output_format"] = output_format
        captured["user"] = user
        return Placement(target_kind="modify_module", target_ref="3",
                         position_hint="Module 3", rationale="fits", confidence=0.8)

    monkeypatch.setattr(client, "_parse", fake_parse)
    structure = CurriculumStructure(modules=[StructureModule(index=3, focus="agents")])
    result = await client.place_gap(_finding(), structure)

    assert captured["output_format"] is Placement
    assert "eval/observability" in captured["user"]   # finding is in the prompt
    assert "Module 3" in captured["user"] or "index" in captured["user"].lower()  # structure is in the prompt
    assert result.target_ref == "3"


@pytest.mark.asyncio
async def test_draft_frame_caps_and_uses_draftframe_format(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["output_format"] = output_format
        return DraftFrame(outline=["a", "b"],
                          sample_assessments=[SampleAssessment(stem="q", kind="mcq", answer_or_rubric="a")],
                          caveats=["verify"])

    monkeypatch.setattr(client, "_parse", fake_parse)
    placement = Placement(target_kind="modify_module", target_ref="3",
                          position_hint="Module 3", rationale="fits", confidence=0.8)
    result = await client.draft_frame(_finding(), placement)

    assert captured["output_format"] is DraftFrame
    assert len(result.sample_assessments) <= 2
