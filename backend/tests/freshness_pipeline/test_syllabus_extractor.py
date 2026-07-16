import pytest
from app.ai.client import AIClient
from app.ai.schemas import SyllabusExtract


def _canned_extract() -> SyllabusExtract:
    return SyllabusExtract(
        course_title="CS294 Agentic AI",
        term="Fall 2025",
        topics=["LLM agents", "tool use", "MCP", "multi-agent systems"],
        notable=["MCP — Model Context Protocol for tool use"],
        quotes=["Students will build production-grade agents"],
        extraction_confidence="high",
    )


@pytest.mark.asyncio
async def test_extract_syllabus_calls_parse_with_correct_output_format(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["output_format"] = output_format
        captured["user"] = user
        return _canned_extract()

    monkeypatch.setattr(client, "_parse", fake_parse)
    page_text = "Week 1: LLM Fundamentals\nWeek 2: Tool use and MCP\nWeek 3: Multi-agent"
    context = "UC Berkeley — CS294 Agentic AI"

    result = await client.extract_syllabus(page_text=page_text, context=context)

    assert captured["output_format"] is SyllabusExtract


@pytest.mark.asyncio
async def test_extract_syllabus_includes_page_text_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_extract()

    monkeypatch.setattr(client, "_parse", fake_parse)
    page_text = "Week 1: LLM Fundamentals\nWeek 2: Tool use and MCP"
    context = "UC Berkeley — CS294 Agentic AI"

    await client.extract_syllabus(page_text=page_text, context=context)

    assert page_text in captured["user"]


@pytest.mark.asyncio
async def test_extract_syllabus_includes_context_in_user_prompt(monkeypatch):
    client = AIClient(api_key="test")
    captured = {}

    async def fake_parse(*, system, user, output_format):
        captured["user"] = user
        return _canned_extract()

    monkeypatch.setattr(client, "_parse", fake_parse)
    page_text = "Some course page content"
    context = "UC Berkeley — CS294 Agentic AI"

    await client.extract_syllabus(page_text=page_text, context=context)

    assert context in captured["user"]


@pytest.mark.asyncio
async def test_extract_syllabus_returns_object_round_trips(monkeypatch):
    client = AIClient(api_key="test")
    canned = _canned_extract()

    async def fake_parse(*, system, user, output_format):
        return canned

    monkeypatch.setattr(client, "_parse", fake_parse)

    result = await client.extract_syllabus(
        page_text="some text", context="Stanford — CS336"
    )

    assert result.course_title == canned.course_title
    assert result.term == canned.term
    assert result.topics == canned.topics
    assert result.notable == canned.notable
    assert result.quotes == canned.quotes
    assert result.extraction_confidence == canned.extraction_confidence
