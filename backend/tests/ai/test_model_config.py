"""Env-configurable AI model (AI_MODEL) + Fable-5 refusal fallback.

Proves:
  * The AIClient model defaults to settings.AI_MODEL, an explicit model= wins.
  * A Fable-5 refusal (stop_reason="refusal", parsed_output=None on a 200) falls
    back to Opus 4.8 ONCE — and BOTH calls are recorded for honest cost
    visibility.
  * No fallback loop when already on the Opus baseline (raises instead).
  * Fable-5 pricing is wired into the cost table.

All via a fake `_client` (no network), reusing the real CategorizeResult output
model for parsed_output. tests/ai/conftest.py already disables usage persistence.
"""

from __future__ import annotations

import logging
import types

import pytest

import app.config
from app.ai import usage_store
from app.ai.builder_advisor import CategorizeResult
from app.ai.client import AIClient
from app.ai.observability import cost_usd, usage


def _make_parsed() -> CategorizeResult:
    return CategorizeResult(
        kind="lesson_plan",
        served_objective_hint="intro",
        estimated_minutes=30,
        complexity=1.0,
        rationale="stub",
    )


def _fake_single(parsed, *, stop_reason="end_turn"):
    """Fake AsyncAnthropic whose messages.parse always returns the same stub."""

    async def _parse(**kwargs):
        return types.SimpleNamespace(
            parsed_output=parsed,
            usage=types.SimpleNamespace(input_tokens=11, output_tokens=22),
            stop_reason=stop_reason,
        )

    return types.SimpleNamespace(messages=types.SimpleNamespace(parse=_parse))


def test_default_model_is_opus():
    assert AIClient(api_key="x").model == "claude-opus-4-8"


def test_explicit_model_overrides():
    assert AIClient(api_key="x", model="claude-fable-5").model == "claude-fable-5"


def test_ai_model_from_settings(monkeypatch):
    # __init__ reads settings.AI_MODEL at construction time, so set it first.
    monkeypatch.setattr(app.config.settings, "AI_MODEL", "claude-fable-5")
    assert AIClient(api_key="x").model == "claude-fable-5"


@pytest.mark.asyncio
async def test_refusal_falls_back_to_opus(caplog):
    usage.reset()
    parsed = _make_parsed()
    calls: list[str] = []

    async def _parse(**kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            # First call (Fable-5): a refusal — 200, no structured output.
            return types.SimpleNamespace(
                parsed_output=None,
                usage=types.SimpleNamespace(input_tokens=5, output_tokens=0),
                stop_reason="refusal",
            )
        # Second call (fallback): a valid result.
        return types.SimpleNamespace(
            parsed_output=parsed,
            usage=types.SimpleNamespace(input_tokens=11, output_tokens=22),
            stop_reason="end_turn",
        )

    client = AIClient(api_key="x", model="claude-fable-5")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(parse=_parse)
    )

    with caplog.at_level(logging.WARNING, logger="app.ai.client"):
        result = await client._parse(
            system="s", user="u", output_format=CategorizeResult
        )

    assert result is parsed
    # Fallback fired: called twice, second call on the Opus baseline.
    assert calls == ["claude-fable-5", "claude-opus-4-8"]
    # Both calls recorded for honest cost visibility.
    assert usage.summary()["total_calls"] == 2
    assert any("refused" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_no_fallback_when_already_opus():
    usage.reset()
    calls: list[str] = []

    async def _parse(**kwargs):
        calls.append(kwargs["model"])
        return types.SimpleNamespace(
            parsed_output=None,
            usage=types.SimpleNamespace(input_tokens=5, output_tokens=0),
            stop_reason="refusal",
        )

    client = AIClient(api_key="x", model="claude-opus-4-8")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(parse=_parse)
    )

    with pytest.raises(ValueError, match="refused"):
        await client._parse(system="s", user="u", output_format=CategorizeResult)

    # No infinite fallback: called exactly once.
    assert calls == ["claude-opus-4-8"]


def test_observability_prices_fable():
    assert cost_usd("claude-fable-5", 1_000_000, 1_000_000) == 60.0
