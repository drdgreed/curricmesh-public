"""GEval + DAG metric factory for the Phase-2 advisory free-text outputs.

Semantic metrics graded over the andragogy/prereq advisory text the
deterministic snapshot eval can't score:

GEval dimensions (all work offline with ``StubJudge``):
  - ``andragogy_groundedness`` — reflects sound adult-learning practice and is
    tailored to the stated learner.
  - ``actionability`` — concrete and actionable, not vague/generic.
  - ``non_hallucination`` — references only objectives/items present in the
    provided course context; invents nothing.
  - ``conciseness`` — high-signal, no padding/repetition/filler.
  - ``advisory_framing`` — framed as a suggestion for a human to decide on,
    never asserting an automatic change.

DAG metric (version-sensitive; needs a LIVE judge — see below):
  - ``advisory_dag`` — a small decision graph: first checks the advice invents
    no entities (hallucination gate), then whether it is concrete/actionable.

INPUT  = the course context. ACTUAL_OUTPUT = the advisory free-text.

The DAG metric uses ``BinaryJudgementNode`` whose verdicts are parsed via
deepeval's ``BinaryJudgementVerdict`` schema (a ``verdict`` bool field). The
offline ``StubJudge`` only satisfies GEval's ``Steps``/``ReasonScore`` schemas,
NOT that verdict schema, so the DAG metric raises offline — the runner catches
that and records it as "n/a (needs live judge)". In LIVE mode it runs for real.
"""

from __future__ import annotations

from deepeval.metrics import DAGMetric, GEval
from deepeval.metrics.dag import (
    BinaryJudgementNode,
    DeepAcyclicGraph,
    VerdictNode,
)
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCaseParams

_THRESHOLD = 0.6

_INPUT = LLMTestCaseParams.INPUT
_ACTUAL = LLMTestCaseParams.ACTUAL_OUTPUT


def _build_geval_metrics(model: DeepEvalBaseLLM) -> list[GEval]:
    """Build the five advisory-output GEval dimensions, all judged by ``model``."""
    return [
        # NOTE (public mirror): proprietary rubric withheld — the production
        # criteria enumerate a specific set of named adult-learning lenses. This
        # generic criteria text keeps the metric (name + threshold) intact.
        GEval(
            name="andragogy_groundedness",
            criteria=(
                "The advice reflects sound adult-learning practice and is tailored "
                "to the stated learner (their experience, role, and goals)."
            ),
            evaluation_params=[_INPUT, _ACTUAL],
            model=model,
            threshold=_THRESHOLD,
        ),
        GEval(
            name="actionability",
            criteria=(
                "The advice is concrete and actionable (a specific change an "
                "author could make), not vague or generic."
            ),
            evaluation_params=[_INPUT, _ACTUAL],
            model=model,
            threshold=_THRESHOLD,
        ),
        GEval(
            name="non_hallucination",
            criteria=(
                "The advice references only objectives/items present in the "
                "provided course context and invents nothing not grounded in it."
            ),
            evaluation_params=[_INPUT, _ACTUAL],
            model=model,
            threshold=_THRESHOLD,
        ),
        GEval(
            name="conciseness",
            criteria=(
                "The advice is concise and high-signal — it makes its point "
                "without padding, repetition, or filler."
            ),
            evaluation_params=[_INPUT, _ACTUAL],
            model=model,
            threshold=_THRESHOLD,
        ),
        GEval(
            name="advisory_framing",
            criteria=(
                "The advice is framed as a suggestion for a human author to "
                "consider and decide on — it never asserts that a change has "
                "been or will be applied automatically."
            ),
            evaluation_params=[_INPUT, _ACTUAL],
            model=model,
            threshold=_THRESHOLD,
        ),
    ]


def _build_dag_metric(model: DeepEvalBaseLLM) -> DAGMetric:
    """Build the small advisory-quality decision-graph DAG metric.

    Graph (2 judgement nodes):
      root: "references only entities in the context (no invented entities)?"
        - false -> VerdictNode(score=0)  # hallucination -> hard fail
        - true  -> inner: "concrete, actionable suggestion (not vague)?"
            - false -> VerdictNode(score=4)  # grounded but vague
            - true  -> VerdictNode(score=10) # grounded AND actionable
    """
    actionable = BinaryJudgementNode(
        criteria=(
            "Is the advice a concrete, actionable suggestion an author could "
            "act on — not vague or generic?"
        ),
        evaluation_params=[_INPUT, _ACTUAL],
        children=[
            VerdictNode(verdict=False, score=4),
            VerdictNode(verdict=True, score=10),
        ],
    )
    grounded = BinaryJudgementNode(
        criteria=(
            "Does the advice reference ONLY objectives/items that appear in the "
            "provided course context (it invents no entities not in the context)?"
        ),
        evaluation_params=[_INPUT, _ACTUAL],
        children=[
            VerdictNode(verdict=False, score=0),
            VerdictNode(verdict=True, child=actionable),
        ],
    )
    dag = DeepAcyclicGraph(root_nodes=[grounded])
    return DAGMetric(name="advisory_dag", dag=dag, model=model, threshold=_THRESHOLD)


def build_metrics(model: DeepEvalBaseLLM, *, include_dag: bool = True) -> list:
    """Build the advisory metrics: five GEval dims + (optionally) the DAG metric.

    ``include_dag=False`` omits the version-sensitive DAG metric (e.g. for the
    GEval-only smoke path). The DAG metric is appended LAST so the GEval scores
    are always produced first; the runner wraps the DAG measure in try/except so
    its offline fragility never blocks the GEval path.
    """
    metrics: list = _build_geval_metrics(model)
    if include_dag:
        metrics.append(_build_dag_metric(model))
    return metrics
