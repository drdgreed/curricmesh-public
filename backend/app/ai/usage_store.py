"""Fire-and-forget persistence of per-call AI usage into ``ai_call_events``.

This is the durable counterpart to the in-process aggregator
(:mod:`app.ai.observability`). One row per AI call, written off the hot path so
the user-facing AI call NEVER waits on — or fails because of — a telemetry
insert.

Contract (best-effort, fail-open):
  * ``record_event`` is a synchronous, non-blocking call made from
    ``AIClient._parse``'s ``finally``. It schedules the DB write as a task on the
    running event loop and returns immediately. No running loop (e.g. a sync
    context) → it silently skips (we can't schedule, and we won't block).
  * The actual insert (``_write_event``) runs in ``_safe_write``, which swallows
    and WARN-logs any exception. A DB outage degrades to "no telemetry row",
    never to a failed AI call.
  * ``PERSIST_ENABLED`` lets the broad test suite disable writes wholesale.

The table is GLOBAL telemetry (non-RLS), so we use a fresh ``AsyncSessionLocal``
session with NO tenant GUC — org attribution is carried explicitly on the row
from the ``current_org`` ContextVar read at schedule time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.ai.observability import cost_usd
from app.database import AsyncSessionLocal
from app.models.ai_usage import AICallEvent
from app.tenant import current_org

logger = logging.getLogger(__name__)

# Tests flip this False to keep unrelated suites from touching the DB.
PERSIST_ENABLED = True

# Hold strong refs to in-flight write tasks so the event loop doesn't GC them
# mid-flight (asyncio only keeps weak refs to tasks).
_pending: set[asyncio.Task[None]] = set()


async def _write_event(event: dict[str, Any]) -> None:
    """Insert one ``AICallEvent`` row in a fresh, detached (non-RLS) session."""
    async with AsyncSessionLocal() as s:
        s.add(
            AICallEvent(
                organization_id=event["organization_id"],
                model=event["model"],
                feature=event["feature"],
                input_tokens=event["input_tokens"],
                output_tokens=event["output_tokens"],
                cost_usd=cost_usd(
                    event["model"],
                    event["input_tokens"],
                    event["output_tokens"],
                ),
                latency_ms=event["latency_ms"],
                stop_reason=event["stop_reason"],
            )
        )
        await s.commit()


async def _safe_write(event: dict[str, Any]) -> None:
    """Run ``_write_event``, swallowing+logging any error — telemetry never throws."""
    try:
        await _write_event(event)
    except Exception:  # noqa: BLE001 — persistence must never surface to the caller
        logger.warning("ai_call_events persist failed", exc_info=True)


def record_event(
    *,
    model: str,
    feature: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    latency_ms: int,
    stop_reason: str | None,
) -> None:
    """Schedule a best-effort durable usage write. Non-blocking; never raises.

    Reads org attribution from the ``current_org`` ContextVar (may be None for
    non-tenant calls). Requires a running event loop to schedule the write; with
    no loop we skip (can't fire-and-forget without one).
    """
    if not PERSIST_ENABLED:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — nothing to schedule onto. Skip (telemetry only).
        return

    org = current_org.get(None)
    event = {
        "organization_id": org,
        "model": model,
        "feature": feature,
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "latency_ms": latency_ms,
        "stop_reason": stop_reason,
    }
    task = loop.create_task(_safe_write(event))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
