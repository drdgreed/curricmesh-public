"""DeepEval semantic-eval runner for the Phase-2 advisory outputs (NON-CI).

Mirrors ``app/ai/eval/run_eval.py``: seam-parameterized by the judge model,
key-gated via ``settings.ANTHROPIC_API_KEY`` (LIVE Anthropic judge vs OFFLINE
STUB), non-blocking by default (exits 0), and writes a markdown report.

Adds (Iteration C):
  - a regression BASELINE at ``baseline.json`` (``{fixture_id: {metric: score}}``)
  - ``--save-baseline`` (env ``DEEP_EVAL_SAVE_BASELINE=1``) to write the current
    scores as the new baseline.
  - otherwise: compare to the baseline and print a REGRESSIONS section for any
    ``(fixture, metric)`` that dropped by more than ``REGRESSION_TOLERANCE``.
  - ``--strict`` (env ``DEEP_EVAL_STRICT=1``) exits 1 when regressions found;
    without it, always exits 0.

DAG graceful handling: the ``advisory_dag`` metric's verdict schema isn't
satisfiable by the OFFLINE ``StubJudge``, so its ``measure`` is wrapped in
try/except — offline it records ``None`` ("n/a (needs live judge)") and the run
continues; LIVE it runs for real.

Run:
  # real scores (needs a key):
  ANTHROPIC_API_KEY=sk-... ./venv/bin/python -m app.ai.eval.deep.run_deep_eval
  # offline pipeline demo (canned scores):
  ./venv/bin/python -m app.ai.eval.deep.run_deep_eval
  # save / compare baseline:
  ./venv/bin/python -m app.ai.eval.deep.run_deep_eval --save-baseline
  ./venv/bin/python -m app.ai.eval.deep.run_deep_eval --strict
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from deepeval.test_case import LLMTestCase

from app.ai.eval.deep import fixtures as fx
from app.ai.eval.deep.judge import AnthropicJudge, StubJudge
from app.ai.eval.deep.metrics import build_metrics
from app.config import settings

_HERE = Path(__file__).resolve().parent
_REPORT_PATH = _HERE / "last_deep_eval_report.md"
_BASELINE_PATH = _HERE / "baseline.json"

# A (fixture, metric) score that drops by MORE than this vs the baseline is a
# regression. Small drops are noise (esp. for a non-deterministic live judge).
REGRESSION_TOLERANCE = 0.1


# ---------------------------------------------------------------------------
# Pure baseline logic (deterministic; unit-tested directly, no judge involved)
# ---------------------------------------------------------------------------


def scores_map(result: dict) -> dict:
    """Flatten a run result into ``{fixture_id: {metric_name: score|None}}``.

    ``fixture_id`` is the 1-based position + label (stable across a run), so the
    baseline survives reordering-free re-runs of the same fixture set.
    """
    out: dict[str, dict] = {}
    for i, row in enumerate(result["rows"], start=1):
        fid = f"{i}. {row['label']}"
        out[fid] = {n: row["cells"][n]["score"] for n in result["metric_names"]}
    return out


def compare_to_baseline(
    current: dict, baseline: dict, tolerance: float
) -> list[dict]:
    """Return regressions: (fixture, metric) whose score dropped > ``tolerance``.

    Pure function — no I/O, no judge. A metric only counts as a regression when
    BOTH the baseline and the current score are numeric and the drop exceeds the
    tolerance. ``None`` scores (e.g. DAG n/a offline) and metrics not present in
    the baseline are skipped (treated as "new", not a regression).
    """
    regressions: list[dict] = []
    for fid, cur_metrics in current.items():
        base_metrics = baseline.get(fid)
        if not base_metrics:
            continue
        for metric, cur_score in cur_metrics.items():
            base_score = base_metrics.get(metric)
            if not isinstance(cur_score, (int, float)):
                continue
            if not isinstance(base_score, (int, float)):
                continue
            drop = base_score - cur_score
            if drop > tolerance:
                regressions.append(
                    {
                        "fixture": fid,
                        "metric": metric,
                        "baseline": base_score,
                        "current": cur_score,
                        "drop": drop,
                    }
                )
    return regressions


def _load_baseline() -> dict | None:
    if not _BASELINE_PATH.exists():
        return None
    try:
        return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_baseline(current: dict) -> None:
    _BASELINE_PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def run_deep_eval(*, judge, cases, mode: str, report_path: str | Path) -> dict:
    """Score every case with every metric, write a markdown report, return results.

    ``judge`` is the DeepEval model seam (AnthropicJudge or StubJudge). ``mode``
    is a human label only. Each metric is run per case via ``metric.measure``;
    the DAG metric is wrapped in try/except so its offline schema fragility
    records a ``None`` score ("n/a") without breaking the GEval path.
    """
    metrics = build_metrics(judge)
    metric_names = [m.name for m in metrics]
    rows: list[dict] = []

    for case in cases:
        tc = LLMTestCase(input=case.context, actual_output=case.output)
        cells: dict[str, dict] = {}
        for metric in metrics:
            try:
                metric.measure(tc)
                cells[metric.name] = {
                    "score": metric.score,
                    "reason": metric.reason,
                }
            except Exception as exc:  # noqa: BLE001 — DAG offline fragility
                cells[metric.name] = {
                    "score": None,
                    "reason": f"n/a (needs live judge): {type(exc).__name__}",
                }
        rows.append({"label": case.label, "cells": cells})

    result = {"mode": mode, "metric_names": metric_names, "rows": rows}
    # Write a report immediately (no regression section yet) so callers that
    # invoke run_deep_eval directly still get last_deep_eval_report.md; main()
    # re-writes it with the regression section once the baseline is compared.
    _write_report(Path(report_path), result, regressions=None)
    return result


def _fmt(score) -> str:
    return f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"


def _write_report(path: Path, result: dict, regressions: list[dict] | None) -> None:
    mode = result["mode"]
    names = result["metric_names"]
    lines: list[str] = []
    lines.append("# CurricMesh Advisory Semantic-Eval (DeepEval) Report")
    lines.append("")
    lines.append(f"**Mode:** {mode}")
    lines.append("")
    if mode.startswith("OFFLINE"):
        lines.append(
            "> Offline STUB mode: the judge returns CANNED scores so the GEval "
            "pipeline runs end-to-end with no API key. The scores below are NOT "
            "meaningful — run with `ANTHROPIC_API_KEY` set for real grading. The "
            "`advisory_dag` metric shows `n/a` offline (its verdict schema needs "
            "a live judge)."
        )
        lines.append("")
    lines.append("## Scores (fixture x metric)")
    lines.append("")
    header = "| Fixture (label) | " + " | ".join(names) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(names)) + " |"
    lines.append(header)
    lines.append(sep)
    for i, row in enumerate(result["rows"], start=1):
        cells = " | ".join(_fmt(row["cells"][n]["score"]) for n in names)
        lines.append(f"| {i}. {row['label']} | {cells} |")
    lines.append("")
    lines.append("## Reasons")
    lines.append("")
    for i, row in enumerate(result["rows"], start=1):
        lines.append(f"### Fixture {i} ({row['label']})")
        lines.append("")
        for n in names:
            c = row["cells"][n]
            lines.append(f"- **{n}** ({_fmt(c['score'])}): {c['reason']}")
        lines.append("")
    # Regression section.
    lines.append("## Regressions vs baseline")
    lines.append("")
    if regressions is None:
        lines.append("_No baseline present — run with `--save-baseline` to set one._")
    elif not regressions:
        lines.append("No regressions vs baseline.")
    else:
        lines.append(
            f"**{len(regressions)} regression(s)** (drop > {REGRESSION_TOLERANCE}):"
        )
        lines.append("")
        for r in regressions:
            lines.append(
                f"- `{r['fixture']}` / **{r['metric']}**: "
                f"{r['baseline']:.2f} -> {r['current']:.2f} "
                f"(drop {r['drop']:.2f})"
            )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_NON-CI, on-demand semantic eval. See README.md._")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _print_summary(result: dict) -> None:
    names = result["metric_names"]
    print(f"=== CurricMesh Advisory Semantic-Eval ({result['mode']}) ===")
    print("| Fixture | " + " | ".join(names) + " |")
    print("| --- | " + " | ".join(["---"] * len(names)) + " |")
    for i, row in enumerate(result["rows"], start=1):
        cells = " | ".join(_fmt(row["cells"][n]["score"]) for n in names)
        print(f"| {i}. {row['label']} | {cells} |")


def main() -> None:
    args = set(sys.argv[1:])
    save_baseline = "--save-baseline" in args or os.environ.get(
        "DEEP_EVAL_SAVE_BASELINE"
    ) == "1"
    strict = "--strict" in args or os.environ.get("DEEP_EVAL_STRICT") == "1"
    # First non-flag positional arg is an optional report path override.
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    report_path = Path(positional[0]) if positional else _REPORT_PATH

    if settings.ANTHROPIC_API_KEY:
        judge = AnthropicJudge()
        mode = "LIVE (Anthropic judge)"
    else:
        judge = StubJudge()
        mode = "OFFLINE STUB — pipeline demo, scores not meaningful"
    print(f"Mode: {mode}")

    cases = fx.load_fixtures()
    result = run_deep_eval(
        judge=judge, cases=cases, mode=mode, report_path=report_path
    )
    _print_summary(result)

    current = scores_map(result)
    regressions: list[dict] | None = None

    if save_baseline:
        _save_baseline(current)
        print(f"Saved baseline -> {_BASELINE_PATH} ({len(current)} fixtures).")
    else:
        baseline = _load_baseline()
        if baseline is None:
            print("No baseline present (run with --save-baseline to set one).")
        else:
            regressions = compare_to_baseline(
                current, baseline, REGRESSION_TOLERANCE
            )
            if regressions:
                print(f"REGRESSIONS ({len(regressions)}):")
                for r in regressions:
                    print(
                        f"  - {r['fixture']} / {r['metric']}: "
                        f"{r['baseline']:.2f} -> {r['current']:.2f} "
                        f"(drop {r['drop']:.2f})"
                    )
            else:
                print("no regressions vs baseline")

    _write_report(report_path, result, regressions)

    if strict and regressions:
        sys.exit(1)
    # Non-blocking philosophy (default): always exit 0.
    sys.exit(0)


if __name__ == "__main__":
    main()
