import pytest
from app.ai.client import AIClient
from app.ai.schemas import GeneratedAssetContent


def _canned_result() -> GeneratedAssetContent:
    return GeneratedAssetContent(
        content=(
            "## MCP Integration\n\n"
            "Model Context Protocol (MCP) is the standard tool-use protocol for agentic AI."
        ),
        summary_of_changes=(
            "Added a new section on MCP integration. "
            "Updated examples to use tool-use patterns from 2026 job postings."
        ),
        caveats=["Verify the exact MCP version number before publishing."],
    )


def _sample_draft_frame() -> dict:
    return {
        "outline": [
            "Introduce MCP protocol basics",
            "Tool registration patterns",
            "End-to-end agent demo",
        ],
        "sample_assessments": [],
        "caveats": [],
    }


def _sample_dossier() -> list[dict]:
    return [
        {
            "run_date": "2026-07-01",
            "evidence": ["MCP adoption in 40% of agentic job postings"],
        }
    ]


@pytest.mark.asyncio
async def test_generate_asset_content_calls_parse_with_correct_output_format(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["system"] = system
        captured["output_format"] = output_format
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    await client.generate_asset_content(
        mode="edit",
        current_content="Existing content about MCP.",
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=["Sample sibling lesson content."],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert captured["output_format"] is GeneratedAssetContent
    # The governed generator prompt must actually be the one sent (T1 review).
    assert "review as a diff" in captured["system"]


@pytest.mark.asyncio
async def test_generate_asset_content_mode_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    await client.generate_asset_content(
        mode="edit",
        current_content="Existing content about MCP.",
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert "edit" in captured["user"]


@pytest.mark.asyncio
async def test_generate_asset_content_current_content_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    current = "Existing content about MCP protocols."
    await client.generate_asset_content(
        mode="edit",
        current_content=current,
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert current in captured["user"]


@pytest.mark.asyncio
async def test_generate_asset_content_draft_frame_outline_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    draft_frame = _sample_draft_frame()
    outline_item = draft_frame["outline"][0]

    await client.generate_asset_content(
        mode="edit",
        current_content="Some content.",
        draft_frame=draft_frame,
        dossier=_sample_dossier(),
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert outline_item in captured["user"]


@pytest.mark.asyncio
async def test_generate_asset_content_dossier_evidence_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    dossier = _sample_dossier()
    evidence_str = "MCP adoption in 40% of agentic job postings"

    await client.generate_asset_content(
        mode="edit",
        current_content="Some content.",
        draft_frame=_sample_draft_frame(),
        dossier=dossier,
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    # The dossier is serialized as JSON — the evidence string must appear
    assert evidence_str in captured["user"]


@pytest.mark.asyncio
async def test_generate_asset_content_style_sample_marker_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    await client.generate_asset_content(
        mode="new",
        current_content=None,
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=["A sibling lesson about agents."],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert "STYLE SAMPLE 1" in captured["user"]


@pytest.mark.asyncio
async def test_generate_asset_content_round_trips_all_fields(monkeypatch):
    client = AIClient(api_key="test")
    canned = _canned_result()

    async def fake_parse(*, system, user, output_format):
        return canned

    monkeypatch.setattr(client, "_parse", fake_parse)

    result = await client.generate_asset_content(
        mode="edit",
        current_content="Existing content.",
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert result.content == canned.content
    assert result.summary_of_changes == canned.summary_of_changes
    assert result.caveats == canned.caveats


@pytest.mark.asyncio
async def test_generate_asset_content_none_current_content_renders_no_current_label(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_result()

    monkeypatch.setattr(client, "_parse", fake_parse)

    await client.generate_asset_content(
        mode="new",
        current_content=None,
        draft_frame=_sample_draft_frame(),
        dossier=_sample_dossier(),
        style_samples=[],
        asset_kind="lesson_plan",
        topic="model context protocol",
    )

    assert "(new asset — no current content)" in captured["user"]
