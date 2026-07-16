"""Router: GET /api/v1/internal/ai-usage — AI-call usage telemetry.

Staff-only window onto AI spend/latency/tokens, blending two sources:

  * the in-process :data:`app.ai.observability.usage` aggregator (live, resets on
    restart) — its fields stay at the top level for backward-compat; and
  * a durable, org-scoped ``persisted`` block read from the ``ai_call_events``
    table (totals, per-model breakdown, and a 14-day daily series for a
    sparkline) filtered to the VIEWER's org.

``ai_call_events`` is GLOBAL (non-RLS) telemetry, so it is queried with the
request session but explicitly filtered to ``current["org"]`` in SQL — that org
scoping is the access boundary here, so a missing org yields a zeroed block
rather than a cross-tenant read.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.observability import usage
from app.auth.rbac import require_roles
from app.database import get_db
from app.models.ai_usage import AICallEvent

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])

# Staff gate — mirror impact.py's module-level require_roles(...) declaration.
_USAGE_ROLES = require_roles("architect", "program_manager")

# Window for the daily sparkline series.
_BY_DAY_DAYS = 14


def _empty_persisted() -> dict[str, Any]:
    """Zeroed persisted block (no org context, or no rows)."""
    return {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "by_model": {},
        "by_day": [],
    }


async def _persisted_for_org(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Durable, org-scoped rollups from ``ai_call_events`` for ``org_id``."""
    where_org = AICallEvent.organization_id == org_id

    # Totals.
    totals_row = (
        await db.execute(
            select(
                func.count(AICallEvent.id),
                func.coalesce(func.sum(AICallEvent.input_tokens), 0),
                func.coalesce(func.sum(AICallEvent.output_tokens), 0),
                func.coalesce(func.sum(AICallEvent.cost_usd), 0),
            ).where(where_org)
        )
    ).one()
    total_calls, total_in, total_out, total_cost = totals_row

    # Per-model breakdown.
    by_model: dict[str, dict[str, Any]] = {}
    model_rows = (
        await db.execute(
            select(
                AICallEvent.model,
                func.count(AICallEvent.id),
                func.coalesce(func.sum(AICallEvent.input_tokens), 0),
                func.coalesce(func.sum(AICallEvent.output_tokens), 0),
                func.coalesce(func.sum(AICallEvent.cost_usd), 0),
            )
            .where(where_org)
            .group_by(AICallEvent.model)
        )
    ).all()
    for model, calls, in_tok, out_tok, cost in model_rows:
        by_model[model] = {
            "calls": int(calls),
            "input_tokens": int(in_tok),
            "output_tokens": int(out_tok),
            "cost_usd": round(float(cost), 4),
        }

    # Last 14 days, grouped by calendar day (in UTC), ascending.
    since = datetime.now(timezone.utc) - timedelta(days=_BY_DAY_DAYS)
    day_col = func.date(AICallEvent.created_at)
    day_rows = (
        await db.execute(
            select(
                day_col,
                func.count(AICallEvent.id),
                func.coalesce(func.sum(AICallEvent.cost_usd), 0),
            )
            .where(where_org, AICallEvent.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    by_day = [
        {
            "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
            "calls": int(calls),
            "cost_usd": round(float(cost), 4),
        }
        for day, calls, cost in day_rows
    ]

    return {
        "total_calls": int(total_calls),
        "total_input_tokens": int(total_in),
        "total_output_tokens": int(total_out),
        "total_cost_usd": round(float(total_cost), 4),
        "by_model": by_model,
        "by_day": by_day,
    }


@router.get("/ai-usage")
async def get_ai_usage(
    current: dict[str, Any] = Depends(_USAGE_ROLES),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return live in-process totals + an org-scoped persisted block. Staff-only."""
    org_raw = current.get("org")
    if not org_raw:
        persisted = _empty_persisted()
    else:
        persisted = await _persisted_for_org(db, uuid.UUID(str(org_raw)))
    return {**usage.summary(), "persisted": persisted}
