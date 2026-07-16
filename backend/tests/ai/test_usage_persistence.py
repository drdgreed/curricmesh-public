"""Durable per-call AI usage: the writer, the _parse hook, and the endpoint block.

Covers:
  * ``_write_event`` / ``record_event`` persist a row with the right fields +
    cost, attribute the org from the ``current_org`` ContextVar, and respect the
    ``PERSIST_ENABLED`` flag.
  * ``AIClient._parse`` calls ``record_event`` with the right kwargs (monkeypatched
    — deterministic, no real create_task/DB).
  * the endpoint's ``persisted`` block is org-scoped (org B excluded) and builds
    by_model + by_day.

``ai_call_events`` is non-RLS, so a plain ``db_session`` (schema via create_all)
suffices. The package-level autouse fixture disables persistence; the writer
tests re-enable it explicitly.
"""

from __future__ import annotations

import types
import uuid

import pytest
from sqlalchemy import select

from app.ai import usage_store
from app.ai.builder_advisor import CategorizeResult
from app.ai.client import AIClient
from app.models.ai_usage import AICallEvent
from app.routers.ai_usage import get_ai_usage
from app.tenant import current_org


@pytest.fixture
def _enable_persistence():
    """Re-enable the writer for tests that own a real session."""
    prev = usage_store.PERSIST_ENABLED
    usage_store.PERSIST_ENABLED = True
    try:
        yield
    finally:
        usage_store.PERSIST_ENABLED = prev


async def _count(db) -> int:
    return len((await db.execute(select(AICallEvent))).scalars().all())


@pytest.mark.asyncio
async def test_write_event_persists_fields_and_cost(db_session):
    org = uuid.uuid4()
    # opus-4-8: 1000/1e6*5 + 2000/1e6*25 = 0.005 + 0.05 = 0.055
    await usage_store._write_event(
        {
            "organization_id": org,
            "model": "claude-opus-4-8",
            "feature": "AdviceReport",
            "input_tokens": 1000,
            "output_tokens": 2000,
            "latency_ms": 1234,
            "stop_reason": "end_turn",
        }
    )

    row = (await db_session.execute(select(AICallEvent))).scalars().one()
    assert row.organization_id == org
    assert row.model == "claude-opus-4-8"
    assert row.feature == "AdviceReport"
    assert row.input_tokens == 1000
    assert row.output_tokens == 2000
    assert float(row.cost_usd) == pytest.approx(0.055)
    assert row.latency_ms == 1234
    assert row.stop_reason == "end_turn"
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_record_event_schedules_and_attributes_org(
    monkeypatch, _enable_persistence
):
    """record_event reads org from current_org and schedules the write.

    We patch ``_write_event`` to capture the event dict (and avoid the global
    engine, which is bound to a different loop than the per-test loop). The real
    DB write + cost is covered by ``test_write_event_persists_fields_and_cost``.
    """
    captured: dict = {}

    async def _fake_write(event):
        captured.update(event)

    monkeypatch.setattr(usage_store, "_write_event", _fake_write)

    org = uuid.uuid4()
    token = current_org.set(org)
    try:
        usage_store.record_event(
            model="claude-haiku-4-5",
            feature="CategorizeResult",
            input_tokens=500,
            output_tokens=1000,
            latency_ms=10,
            stop_reason="end_turn",
        )
        # record_event schedules a task on the running loop; drain it.
        assert usage_store._pending
        for task in list(usage_store._pending):
            await task
    finally:
        current_org.reset(token)

    assert captured["organization_id"] == org
    assert captured["model"] == "claude-haiku-4-5"
    assert captured["feature"] == "CategorizeResult"
    assert captured["input_tokens"] == 500
    assert captured["output_tokens"] == 1000


@pytest.mark.asyncio
async def test_persist_disabled_writes_nothing(db_session):
    # autouse package fixture already disabled persistence.
    assert usage_store.PERSIST_ENABLED is False
    usage_store.record_event(
        model="claude-opus-4-8",
        feature="AdviceReport",
        input_tokens=10,
        output_tokens=10,
        latency_ms=1,
        stop_reason="end_turn",
    )
    assert not usage_store._pending
    assert await _count(db_session) == 0


def _make_fake_client(parsed):
    async def _parse(**kwargs):
        u = types.SimpleNamespace(input_tokens=11, output_tokens=22)
        return types.SimpleNamespace(
            parsed_output=parsed, usage=u, stop_reason="end_turn"
        )

    messages = types.SimpleNamespace(parse=_parse)
    return types.SimpleNamespace(messages=messages)


@pytest.mark.asyncio
async def test_parse_calls_record_event(monkeypatch):
    captured: dict = {}

    def _fake_record_event(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(usage_store, "record_event", _fake_record_event)

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

    assert captured["model"] == "claude-opus-4-8"
    assert captured["feature"] == "CategorizeResult"
    assert captured["input_tokens"] == 11
    assert captured["output_tokens"] == 22
    assert captured["stop_reason"] == "end_turn"
    assert isinstance(captured["latency_ms"], int)


def _insert(db, *, org, model, in_tok, out_tok, cost):
    db.add(
        AICallEvent(
            organization_id=org,
            model=model,
            feature="AdviceReport",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=100,
            stop_reason="end_turn",
        )
    )


@pytest.mark.asyncio
async def test_endpoint_persisted_block_is_org_scoped(db_session):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    _insert(db_session, org=org_a, model="claude-opus-4-8", in_tok=1000, out_tok=2000, cost=0.055)
    _insert(db_session, org=org_a, model="claude-opus-4-8", in_tok=500, out_tok=500, cost=0.015)
    _insert(db_session, org=org_a, model="claude-haiku-4-5", in_tok=200, out_tok=100, cost=0.0007)
    # org B row — must be excluded.
    _insert(db_session, org=org_b, model="claude-opus-4-8", in_tok=9999, out_tok=9999, cost=9.0)
    await db_session.commit()

    result = await get_ai_usage(
        current={"role": "architect", "org": str(org_a)}, db=db_session
    )
    p = result["persisted"]
    assert p["total_calls"] == 3
    assert p["total_input_tokens"] == 1700
    assert p["total_output_tokens"] == 2600
    assert p["total_cost_usd"] == pytest.approx(0.0707, abs=1e-4)

    assert p["by_model"]["claude-opus-4-8"]["calls"] == 2
    assert p["by_model"]["claude-opus-4-8"]["input_tokens"] == 1500
    assert p["by_model"]["claude-haiku-4-5"]["calls"] == 1
    assert "claude-haiku-4-5" in p["by_model"]

    # by_day: all rows are "today" → one ascending bucket covering all 3 calls.
    assert len(p["by_day"]) >= 1
    assert sum(d["calls"] for d in p["by_day"]) == 3
    for d in p["by_day"]:
        assert set(d.keys()) == {"date", "calls", "cost_usd"}


@pytest.mark.asyncio
async def test_endpoint_no_org_returns_zeroed_block(db_session):
    result = await get_ai_usage(current={"role": "architect"}, db=db_session)
    assert result["persisted"]["total_calls"] == 0
    assert result["persisted"]["by_model"] == {}
    assert result["persisted"]["by_day"] == []
