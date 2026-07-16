"""Part A: per-call AI observability log.

Proves ``AIClient._parse`` emits one structured INFO line per call carrying the
model, output format, token usage, latency, and stop reason — WITHOUT any
network. We swap ``client._client`` for a fake whose ``messages.parse`` is an
async fn returning a stub response (a real pydantic ``parsed_output`` instance
plus ``.usage`` / ``.stop_reason``).
"""

from __future__ import annotations

import logging
import types

import pytest

from app.ai.builder_advisor import CategorizeResult
from app.ai.client import AIClient


def _make_fake_client(parsed):
    """Build a fake AsyncAnthropic-shaped object: ``.messages.parse`` is async."""

    async def _parse(**kwargs):
        usage = types.SimpleNamespace(input_tokens=11, output_tokens=22)
        return types.SimpleNamespace(
            parsed_output=parsed, usage=usage, stop_reason="end_turn"
        )

    messages = types.SimpleNamespace(parse=_parse)
    return types.SimpleNamespace(messages=messages)


@pytest.mark.asyncio
async def test_parse_emits_observability_log(caplog):
    parsed = CategorizeResult(
        kind="lesson_plan",
        served_objective_hint="intro",
        estimated_minutes=30,
        complexity=1.0,
        rationale="stub",
    )
    client = AIClient(api_key="x")
    # Inject the fake transport — no real anthropic.AsyncAnthropic, no network.
    client._client = _make_fake_client(parsed)

    with caplog.at_level(logging.INFO, logger="app.ai.client"):
        result = await client._parse(
            system="s", user="u", output_format=CategorizeResult
        )

    assert result is parsed

    ai_lines = [r.getMessage() for r in caplog.records if "ai_call" in r.getMessage()]
    assert ai_lines, "expected an ai_call observability line"
    line = ai_lines[0]
    assert "in_tokens=11" in line
    assert "out_tokens=22" in line
    assert "stop=end_turn" in line
    assert "model=claude-opus-4-8" in line
    assert "latency_ms=" in line


@pytest.mark.asyncio
async def test_parse_logs_latency_even_when_output_none(caplog):
    """The finally-based log must fire (with latency) before the None ValueError."""
    client = AIClient(api_key="x")
    client._client = _make_fake_client(None)  # parsed_output is None

    with caplog.at_level(logging.INFO, logger="app.ai.client"):
        with pytest.raises(ValueError):
            await client._parse(
                system="s", user="u", output_format=CategorizeResult
            )

    assert any("ai_call" in r.getMessage() for r in caplog.records)
