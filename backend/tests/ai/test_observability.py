"""Iteration 1: in-process AI usage aggregator + /internal/ai-usage endpoint.

Offline — no network, no DB. Exercises the pure-stdlib aggregator (token/latency/
cost accumulation, percentiles, per-model + stop-reason breakdown), the unknown-
model and None-token edge cases, the endpoint handler directly, and that
``AIClient._parse`` feeds the aggregator (reusing the fake-transport pattern).
"""

from __future__ import annotations

import types

import pytest

from app.ai.builder_advisor import CategorizeResult
from app.ai.client import AIClient
from app.ai.observability import AIUsageAggregator, usage
from app.routers.ai_usage import get_ai_usage


def test_aggregator_records_and_summarizes():
    usage.reset()
    # opus-4-8: input 5.0 / output 25.0 per 1e6 tokens.
    usage.record(
        model="claude-opus-4-8",
        input_tokens=1000,
        output_tokens=2000,
        latency_ms=100,
        stop_reason="end_turn",
    )
    # haiku-4-5: input 1.0 / output 5.0 per 1e6 tokens.
    usage.record(
        model="claude-haiku-4-5",
        input_tokens=500,
        output_tokens=1000,
        latency_ms=300,
        stop_reason="end_turn",
    )
    usage.record(
        model="claude-haiku-4-5",
        input_tokens=500,
        output_tokens=0,
        latency_ms=200,
        stop_reason="max_tokens",
    )

    s = usage.summary()
    assert s["total_calls"] == 3
    assert s["total_input_tokens"] == 2000
    assert s["total_output_tokens"] == 3000

    # Cost math:
    #   opus-4-8: 1000/1e6*5 + 2000/1e6*25 = 0.005 + 0.05 = 0.055
    #   haiku call 1: 500/1e6*1 + 1000/1e6*5 = 0.0005 + 0.005 = 0.0055
    #   haiku call 2: 500/1e6*1 + 0 = 0.0005
    # total = 0.055 + 0.0055 + 0.0005 = 0.0610
    assert s["total_cost_usd"] == pytest.approx(0.0610)

    by_model = s["by_model"]
    assert by_model["claude-opus-4-8"] == {
        "calls": 1,
        "input_tokens": 1000,
        "output_tokens": 2000,
        "cost_usd": pytest.approx(0.055),
    }
    assert by_model["claude-haiku-4-5"]["calls"] == 2
    assert by_model["claude-haiku-4-5"]["input_tokens"] == 1000
    assert by_model["claude-haiku-4-5"]["output_tokens"] == 1000
    assert by_model["claude-haiku-4-5"]["cost_usd"] == pytest.approx(0.006)

    assert s["stop_reasons"] == {"end_turn": 2, "max_tokens": 1}

    lat = s["latency_ms"]
    # latencies = [100, 300, 200]; sorted [100, 200, 300].
    assert lat["max"] == 300
    assert 100 <= lat["p50"] <= 300
    assert lat["p95"] == 300


def test_unknown_model_zero_cost():
    agg = AIUsageAggregator()
    agg.record(
        model="some-future-model",
        input_tokens=9999,
        output_tokens=9999,
        latency_ms=50,
        stop_reason="end_turn",
    )
    s = agg.summary()
    assert s["total_calls"] == 1
    assert s["total_input_tokens"] == 9999
    assert s["total_output_tokens"] == 9999
    assert s["total_cost_usd"] == 0.0
    assert s["by_model"]["some-future-model"]["cost_usd"] == 0.0


def test_none_tokens_safe():
    agg = AIUsageAggregator()
    agg.record(
        model="claude-opus-4-8",
        input_tokens=None,
        output_tokens=None,
        latency_ms=10,
        stop_reason=None,
    )
    s = agg.summary()
    assert s["total_calls"] == 1
    assert s["total_input_tokens"] == 0
    assert s["total_output_tokens"] == 0
    assert s["total_cost_usd"] == 0.0
    # None stop reason should still be counted under a stable key.
    assert sum(s["stop_reasons"].values()) == 1


def test_empty_summary_latency_zero():
    agg = AIUsageAggregator()
    s = agg.summary()
    assert s["total_calls"] == 0
    assert s["latency_ms"] == {"p50": 0, "p95": 0, "max": 0}


@pytest.mark.asyncio
async def test_endpoint_returns_summary():
    usage.reset()
    usage.record(
        model="claude-opus-4-8",
        input_tokens=100,
        output_tokens=200,
        latency_ms=42,
        stop_reason="end_turn",
    )
    # Call the handler directly with a fake authenticated staff principal. No
    # "org" key → the persisted block is zeroed and the db arg is never touched.
    result = await get_ai_usage(current={"role": "architect"}, db=None)
    assert result["total_calls"] == 1
    assert result["total_input_tokens"] == 100
    assert set(result.keys()) >= {
        "total_calls",
        "total_input_tokens",
        "total_output_tokens",
        "total_cost_usd",
        "latency_ms",
        "by_model",
        "stop_reasons",
        "persisted",
    }
    # No org context → zeroed persisted block (no crash).
    assert result["persisted"]["total_calls"] == 0
    assert result["persisted"]["by_day"] == []


def _make_fake_client(parsed):
    async def _parse(**kwargs):
        u = types.SimpleNamespace(input_tokens=11, output_tokens=22)
        return types.SimpleNamespace(
            parsed_output=parsed, usage=u, stop_reason="end_turn"
        )

    messages = types.SimpleNamespace(parse=_parse)
    return types.SimpleNamespace(messages=messages)


@pytest.mark.asyncio
async def test_parse_feeds_aggregator():
    usage.reset()
    parsed = CategorizeResult(
        kind="lesson_plan",
        served_objective_hint="intro",
        estimated_minutes=30,
        complexity=1.0,
        rationale="stub",
    )
    client = AIClient(api_key="x")
    client._client = _make_fake_client(parsed)

    await client._parse(system="s", user="u", output_format=CategorizeResult)

    s = usage.summary()
    assert s["total_calls"] == 1
    assert s["total_input_tokens"] == 11
    assert s["total_output_tokens"] == 22
    assert s["by_model"]["claude-opus-4-8"]["calls"] == 1
