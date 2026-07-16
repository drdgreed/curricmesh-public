"""Smoke test for the NON-CI DeepEval advisory semantic-eval slice.

Skipped entirely when ``deepeval`` is not installed (it is NOT in the default
install), so CI stays offline/green. When the ``[eval]`` extra IS installed,
this proves the OFFLINE STUB pipeline constructs, runs through GEval, produces a
score per metric, and writes the report — with NO API key / network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("deepeval")

from deepeval.test_case import LLMTestCase  # noqa: E402

from app.ai.eval.deep import fixtures as fx  # noqa: E402
from app.ai.eval.deep.judge import StubJudge  # noqa: E402
from app.ai.eval.deep.metrics import build_metrics  # noqa: E402
from app.ai.eval.deep.run_deep_eval import run_deep_eval  # noqa: E402


def test_build_metrics_geval_only_returns_five_dims():
    # include_dag=False yields just the offline-safe GEval dimensions.
    metrics = build_metrics(StubJudge(), include_dag=False)
    assert [m.name for m in metrics] == [
        "andragogy_groundedness",
        "actionability",
        "non_hallucination",
        "conciseness",
        "advisory_framing",
    ]


def test_build_metrics_includes_dag_by_default():
    metrics = build_metrics(StubJudge())
    assert [m.name for m in metrics][-1] == "advisory_dag"


def test_stub_pipeline_scores_every_geval_metric():
    """Each GEval dim through the stub yields a numeric score, no network."""
    judge = StubJudge()
    metrics = build_metrics(judge, include_dag=False)
    tc = LLMTestCase(input=fx.CASES[0].context, actual_output=fx.CASES[0].output)
    for metric in metrics:
        metric.measure(tc)
        assert isinstance(metric.score, (int, float))


def test_runner_offline_writes_report(tmp_path):
    report = tmp_path / "deep_report.md"
    result = run_deep_eval(
        judge=StubJudge(),
        cases=fx.CASES,
        mode="OFFLINE STUB — test",
        report_path=report,
    )
    assert report.exists()
    assert len(result["rows"]) == len(fx.CASES)
    # Every fixture has a numeric score for every GEval dim; the DAG metric is
    # allowed to be None (n/a) offline since the stub can't satisfy its schema.
    geval_dims = [n for n in result["metric_names"] if n != "advisory_dag"]
    for row in result["rows"]:
        for name in geval_dims:
            assert isinstance(row["cells"][name]["score"], (int, float))
