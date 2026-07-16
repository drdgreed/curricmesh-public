"""Sample advisory free-text cases for the DeepEval semantic eval.

Each case is ``{context, output, label}`` where ``context`` is the course
context fed to the andragogy advisor and ``output`` is the advisory free-text
being graded. ``label`` is a human expectation (STRONG / WEAK) so the metrics
visibly discriminate — at least one clearly grounded+actionable+faithful case
and one clearly vague-or-hallucinated case.

Content is plausible CurricMesh material (course objectives + an andragogy note).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CAPTURED_PATH = Path(__file__).resolve().parent / "fixtures" / "captured_advisory.json"

_CONTEXT_DEVOPS = (
    "COURSE: Cloud Platform Engineering for Working Engineers (8 weeks)\n"
    "LEARNER PROFILE: mid-level backend engineers, 3-5 yrs experience, "
    "5 hrs/week, goal = ship and operate production services on Kubernetes.\n"
    "OBJECTIVES:\n"
    "  1. Containerize a service and deploy it to a managed Kubernetes cluster.\n"
    "  2. Define infrastructure as code with Terraform.\n"
    "  3. Build a CI/CD pipeline that runs tests and deploys on merge.\n"
    "  4. Instrument a service with metrics, logs, and traces.\n"
    "ITEMS: Week 1 'Containers & Images' (lab), Week 3 'Terraform Basics' "
    "(lab), Week 5 'CI/CD Pipeline' (project), Week 7 'Observability' (lab)."
)

_CONTEXT_DATA = (
    "COURSE: Practical Data Modeling for Analysts (6 weeks)\n"
    "LEARNER PROFILE: data analysts, 2-4 yrs experience, SQL-fluent, "
    "4 hrs/week, goal = design schemas that survive real reporting needs.\n"
    "OBJECTIVES:\n"
    "  1. Normalize a transactional schema to 3NF.\n"
    "  2. Design a star schema for a reporting workload.\n"
    "  3. Choose indexes for common query patterns.\n"
    "ITEMS: Week 1 'Normalization' (lesson_plan), Week 3 'Dimensional "
    "Modeling' (lesson_plan), Week 5 'Indexing Strategy' (lab)."
)


@dataclass(frozen=True)
class DeepEvalCase:
    context: str
    output: str
    label: str  # "STRONG" or "WEAK" — human expectation only


CASES: list[DeepEvalCase] = [
    # STRONG: grounded in andragogy (prior experience, problem-centered,
    # need-to-know), tailored to the learner, concrete/actionable, faithful to
    # the listed objectives/items.
    DeepEvalCase(
        label="STRONG",
        context=_CONTEXT_DEVOPS,
        output=(
            "These are working engineers with 3-5 years shipping backends, so "
            "activate that prior experience: in Week 1 'Containers & Images', "
            "open by having learners containerize a service THEY already "
            "maintain rather than a toy app — this taps the 'prior experience' "
            "and 'readiness tied to real tasks' principles. The Week 5 'CI/CD "
            "Pipeline' project is the right problem-centered anchor; make its "
            "deliverable the pipeline for that same service so Objectives 1, 3, "
            "and 4 compound on one artifact instead of four disconnected labs. "
            "To surface 'need-to-know' upfront, add a one-paragraph 'why this "
            "matters in an on-call rotation' note to the Week 7 'Observability' "
            "lab tying metrics/logs/traces (Objective 4) to a real incident."
        ),
    ),
    # WEAK (vague/generic): platitudes, no specific change, not tailored.
    DeepEvalCase(
        label="WEAK",
        context=_CONTEXT_DEVOPS,
        output=(
            "Make sure the course is engaging and learner-centered. Adults learn "
            "best when motivated, so keep things interesting and relevant. "
            "Consider adding more hands-on activities and real-world examples. "
            "Good content and clear objectives are important for success."
        ),
    ),
    # STRONG: faithful, concrete, andragogy-grounded for the data course.
    DeepEvalCase(
        label="STRONG",
        context=_CONTEXT_DATA,
        output=(
            "Your analysts are SQL-fluent with real reporting scars, so lead "
            "Week 3 'Dimensional Modeling' with a star schema they reverse-"
            "engineer from a report they've actually been asked to build — this "
            "is problem-centered and leverages their prior experience. Tie the "
            "Week 5 'Indexing Strategy' lab directly to Objective 3 by having "
            "them index the exact star schema from Week 3, so 'readiness tied to "
            "real tasks' is satisfied and the three objectives build on one "
            "running example rather than separate toy schemas."
        ),
    ),
    # WEAK (hallucinated): references items/objectives NOT in the context
    # (a 'Week 9 Machine Learning module', 'Spark', a capstone) — should score
    # low on non_hallucination.
    DeepEvalCase(
        label="WEAK",
        context=_CONTEXT_DATA,
        output=(
            "Strengthen the Week 9 'Machine Learning Pipelines' module by adding "
            "a Spark streaming lab, and make the final capstone a real-time "
            "recommender system. The 'Data Governance' objective should be moved "
            "earlier, and the Kubernetes deployment unit needs more depth."
        ),
    ),
]


def load_fixtures() -> list[DeepEvalCase]:
    """Return the cases to grade, preferring captured REAL engine outputs.

    Source precedence:
      1. ``fixtures/captured_advisory.json`` (real engine outputs snapshotted by
         ``capture.py``) when it exists and is non-empty.
      2. The hand-written ``CASES`` above (STRONG/WEAK demonstration cases).

    Captured records carry ``{context, output, source, kind}``; we map ``source``
    (e.g. "advise"/"infer") to the ``label`` so the report stays readable. Prints
    which source was used so a runner's mode line is unambiguous.
    """
    if _CAPTURED_PATH.exists():
        try:
            raw = json.loads(_CAPTURED_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = []
        cases = [
            DeepEvalCase(
                context=rec["context"],
                output=rec["output"],
                label=rec.get("source") or rec.get("kind") or "captured",
            )
            for rec in raw
            if rec.get("context") and rec.get("output")
        ]
        if cases:
            print(
                f"[fixtures] using {len(cases)} CAPTURED real-output fixtures "
                f"from {_CAPTURED_PATH.name}"
            )
            return cases

    print(
        f"[fixtures] using {len(CASES)} hand-written fixtures "
        "(no captured_advisory.json — run `python -m app.ai.eval.deep.capture` "
        "with a key to snapshot real outputs)"
    )
    return list(CASES)
