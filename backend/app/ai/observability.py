"""In-process AI-call usage aggregator (tokens / latency / cost).

Iteration 1 of the eval/observability prototype. A module-level singleton
(:data:`usage`) records every AI call made through ``AIClient._parse`` and
exposes a rolling summary: total calls, token sums, an estimated USD cost, a
per-model breakdown, stop-reason counts, and latency percentiles over a bounded
recent window.

This is LIVE TELEMETRY, not persistence — it lives in process memory and resets
on restart. That's intentional: it's cheap, dependency-free, and good enough to
answer "how much are we spending / how slow are calls right now". For durable
analytics you'd write to a store; that's out of scope here (YAGNI).

Pure stdlib. A single :class:`threading.Lock` guards record/summary so the deque
and dict mutations stay internally consistent even though we run under one
asyncio loop.
"""

from __future__ import annotations

import math
import threading
from collections import deque

# USD per 1,000,000 tokens, as (input_price, output_price). Unknown models fall
# back to (0.0, 0.0) — we still count their tokens, we just can't price them.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_RECENT_LATENCY_MAXLEN = 1000


def cost_usd(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float:
    """Estimated USD cost of one call from the shared ``_PRICING`` table.

    Single source of truth for per-call pricing — reused by the in-process
    aggregator AND the durable usage writer (DRY). Unknown model → 0.0; ``None``
    token counts are treated as 0.
    """
    in_tok = input_tokens or 0
    out_tok = output_tokens or 0
    in_price, out_price = _PRICING.get(model, (0.0, 0.0))
    return (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Nearest-rank percentile of an already-sorted list (``pct`` in [0, 100]).

    Index = ceil(pct/100 * n), clamped to [1, n], then 0-based. Returns 0 for an
    empty list. No numpy — this is the whole point of the module.
    """
    n = len(sorted_values)
    if n == 0:
        return 0
    rank = math.ceil((pct / 100.0) * n)
    rank = max(1, min(rank, n))
    return sorted_values[rank - 1]


class AIUsageAggregator:
    """Accumulates AI-call telemetry in process memory. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Clear all accumulated state (used by tests and could back an admin reset)."""
        with self._lock:
            self.total_calls = 0
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.total_cost_usd = 0.0
            self.unknown_token_calls = 0
            self._recent_latency: deque[int] = deque(maxlen=_RECENT_LATENCY_MAXLEN)
            # model -> {calls, input_tokens, output_tokens, cost_usd}
            self._by_model: dict[str, dict[str, float]] = {}
            # stop_reason -> count
            self._stop_reasons: dict[str, int] = {}

    def record(
        self,
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        stop_reason: str | None,
    ) -> None:
        """Record one AI call. ``None`` token counts are treated as 0 for sums."""
        in_tok = input_tokens or 0
        out_tok = output_tokens or 0
        if input_tokens is None or output_tokens is None:
            unknown = True
        else:
            unknown = False

        cost = cost_usd(model, input_tokens, output_tokens)

        with self._lock:
            self.total_calls += 1
            self.total_input_tokens += in_tok
            self.total_output_tokens += out_tok
            self.total_cost_usd += cost
            if unknown:
                self.unknown_token_calls += 1

            self._recent_latency.append(int(latency_ms))

            m = self._by_model.get(model)
            if m is None:
                m = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
                self._by_model[model] = m
            m["calls"] += 1
            m["input_tokens"] += in_tok
            m["output_tokens"] += out_tok
            m["cost_usd"] += cost

            key = stop_reason if stop_reason is not None else "unknown"
            self._stop_reasons[key] = self._stop_reasons.get(key, 0) + 1

    def summary(self) -> dict:
        """Snapshot the current totals + breakdowns as a JSON-serializable dict."""
        with self._lock:
            latencies = sorted(self._recent_latency)
            by_model = {
                model: {
                    "calls": int(v["calls"]),
                    "input_tokens": int(v["input_tokens"]),
                    "output_tokens": int(v["output_tokens"]),
                    "cost_usd": round(v["cost_usd"], 4),
                }
                for model, v in self._by_model.items()
            }
            return {
                "total_calls": self.total_calls,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cost_usd": round(self.total_cost_usd, 4),
                "unknown_token_calls": self.unknown_token_calls,
                "latency_ms": {
                    "p50": _percentile(latencies, 50),
                    "p95": _percentile(latencies, 95),
                    "max": latencies[-1] if latencies else 0,
                },
                "by_model": by_model,
                "stop_reasons": dict(self._stop_reasons),
            }


# Module-level singleton — import this, not the class.
usage = AIUsageAggregator()
