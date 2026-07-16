"""Expanded NON-CI DeepEval slice tests (Iterations A–D).

Skipped entirely when ``deepeval`` is not installed, so CI stays offline/green.
Covers: the two new GEval dimensions score offline; the runner runs end-to-end
offline (DAG may be n/a) writing the report; the pure baseline-compare logic;
and the ``capture.py`` key-gate (no key -> clean exit, writes nothing).
"""

from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

from deepeval.test_case import LLMTestCase  # noqa: E402

from app.ai.eval.deep import fixtures as fx  # noqa: E402
from app.ai.eval.deep.judge import StubJudge  # noqa: E402
from app.ai.eval.deep.metrics import (  # noqa: E402
    _build_dag_metric,
    build_metrics,
)
from app.ai.eval.deep.run_deep_eval import (  # noqa: E402
    REGRESSION_TOLERANCE,
    compare_to_baseline,
    run_deep_eval,
    scores_map,
)


# --- Iteration A: the two new GEval dimensions score offline ----------------


def test_new_geval_dimensions_build_and_score():
    judge = StubJudge()
    metrics = build_metrics(judge, include_dag=False)
    by_name = {m.name: m for m in metrics}
    assert "conciseness" in by_name
    assert "advisory_framing" in by_name

    tc = LLMTestCase(input=fx.CASES[0].context, actual_output=fx.CASES[0].output)
    for name in ("conciseness", "advisory_framing"):
        metric = by_name[name]
        metric.measure(tc)
        assert isinstance(metric.score, (int, float))


def test_dag_metric_constructs():
    # The DAG metric must at least CONSTRUCT offline (it only fails at measure
    # time when the stub can't satisfy the verdict schema).
    metric = _build_dag_metric(StubJudge())
    assert metric.name == "advisory_dag"


# --- Iteration C/D: runner runs end-to-end offline, DAG n/a -----------------


def test_runner_offline_end_to_end(tmp_path):
    report = tmp_path / "deep_report.md"
    result = run_deep_eval(
        judge=StubJudge(),
        cases=fx.CASES,
        mode="OFFLINE STUB — test",
        report_path=report,
    )
    assert report.exists()
    assert len(result["rows"]) == len(fx.CASES)
    # GEval dims always produce numeric scores; DAG is allowed to be None (n/a).
    geval_dims = [n for n in result["metric_names"] if n != "advisory_dag"]
    for row in result["rows"]:
        for name in geval_dims:
            assert isinstance(row["cells"][name]["score"], (int, float))
    # The DAG metric is present and (offline) n/a.
    assert "advisory_dag" in result["metric_names"]
    for row in result["rows"]:
        assert row["cells"]["advisory_dag"]["score"] is None


# --- Iteration D: pure baseline-compare logic (no judge) --------------------


def test_compare_identical_no_regressions():
    current = {"1. A": {"m": 0.8}, "2. B": {"m": 0.5}}
    assert compare_to_baseline(current, current, REGRESSION_TOLERANCE) == []


def test_compare_big_drop_flags_one_regression():
    baseline = {"1. A": {"m": 0.8}}
    current = {"1. A": {"m": 0.5}}  # drop 0.3 > 0.1
    regs = compare_to_baseline(current, baseline, REGRESSION_TOLERANCE)
    assert len(regs) == 1
    assert regs[0]["fixture"] == "1. A"
    assert regs[0]["metric"] == "m"
    assert regs[0]["drop"] == pytest.approx(0.3)


def test_compare_small_drop_not_flagged():
    baseline = {"1. A": {"m": 0.8}}
    current = {"1. A": {"m": 0.75}}  # drop 0.05 < 0.1
    assert compare_to_baseline(current, baseline, REGRESSION_TOLERANCE) == []


def test_compare_new_metric_not_flagged():
    baseline = {"1. A": {"m": 0.8}}
    current = {"1. A": {"m": 0.8, "new_metric": 0.1}}  # new_metric absent from base
    assert compare_to_baseline(current, baseline, REGRESSION_TOLERANCE) == []


def test_compare_none_scores_skipped():
    # A DAG-style None score (n/a) must never be treated as a regression.
    baseline = {"1. A": {"advisory_dag": 0.9}}
    current = {"1. A": {"advisory_dag": None}}
    assert compare_to_baseline(current, baseline, REGRESSION_TOLERANCE) == []


def test_scores_map_shape():
    result = {
        "metric_names": ["m1", "m2"],
        "rows": [
            {"label": "STRONG", "cells": {"m1": {"score": 0.8}, "m2": {"score": None}}},
        ],
    }
    sm = scores_map(result)
    assert sm == {"1. STRONG": {"m1": 0.8, "m2": None}}


# --- Iteration B/D: capture.py key-gate -------------------------------------


def test_capture_skips_without_key(monkeypatch, capsys):
    from app.ai.eval.deep import capture

    monkeypatch.setattr(capture.settings, "ANTHROPIC_API_KEY", "")
    # Ensure no file is written even if a stale one is somewhere — capture only
    # writes after the key check, so a clean exit proves nothing was written.
    with pytest.raises(SystemExit) as exc:
        capture.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "skipping" in out.lower()
