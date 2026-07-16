"""Capture REAL engine outputs as DeepEval fixtures (NON-CI, key-gated).

Run:
  ANTHROPIC_API_KEY=sk-... ./venv/bin/python -m app.ai.eval.deep.capture

What it does: with a real ``ANTHROPIC_API_KEY``, builds a few representative
``course_context`` + ``learner_profile`` cases in-code (no DB), calls the REAL
engines (``AIClient.advise`` and ``AIClient.infer``), and snapshots the returned
free-text — the andragogy advice note texts and the prereq reasons — into
``fixtures/captured_advisory.json`` as a list of
``{context, output, source, kind}`` records. This makes the semantic eval grade
what the system ACTUALLY produces, not hand-authored stand-ins.

Without a key it prints a clear skip message, writes nothing, and exits 0 — so
it never breaks the offline/CI story.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.config import settings

_OUT_PATH = Path(__file__).resolve().parent / "fixtures" / "captured_advisory.json"


# Hand-authored, plausible CurricMesh course contexts (objectives + items by
# week) + matching learner profiles. These feed the REAL engines; only the
# engine OUTPUTS are snapshotted as fixtures.
_CASES: list[dict] = [
    {
        "course_context": (
            "COURSE: Cloud Platform Engineering for Working Engineers (8 weeks)\n"
            "OBJECTIVES:\n"
            "  1. Containerize a service and deploy it to a managed Kubernetes cluster.\n"
            "  2. Define infrastructure as code with Terraform.\n"
            "  3. Build a CI/CD pipeline that runs tests and deploys on merge.\n"
            "  4. Instrument a service with metrics, logs, and traces.\n"
            "ITEMS: Week 1 'Containers & Images' (lab), Week 3 'Terraform Basics' "
            "(lab), Week 5 'CI/CD Pipeline' (project), Week 7 'Observability' (lab)."
        ),
        "learner_profile": {
            "experience": "mid-level backend engineers, 3-5 yrs",
            "weekly_hours": "5",
            "goal": "ship and operate production services on Kubernetes",
            "motivation": "career growth, on-call confidence",
        },
        "items": [
            {"title": "Containers & Images", "kind": "lab", "week": 1},
            {"title": "Terraform Basics", "kind": "lab", "week": 3},
            {"title": "CI/CD Pipeline", "kind": "project", "week": 5},
            {"title": "Observability", "kind": "lab", "week": 7},
        ],
    },
    {
        "course_context": (
            "COURSE: Practical Data Modeling for Analysts (6 weeks)\n"
            "OBJECTIVES:\n"
            "  1. Normalize a transactional schema to 3NF.\n"
            "  2. Design a star schema for a reporting workload.\n"
            "  3. Choose indexes for common query patterns.\n"
            "ITEMS: Week 1 'Normalization' (lesson_plan), Week 3 'Dimensional "
            "Modeling' (lesson_plan), Week 5 'Indexing Strategy' (lab)."
        ),
        "learner_profile": {
            "experience": "data analysts, 2-4 yrs, SQL-fluent",
            "weekly_hours": "4",
            "goal": "design schemas that survive real reporting needs",
        },
        "items": [
            {"title": "Normalization", "kind": "lesson_plan", "week": 1},
            {"title": "Dimensional Modeling", "kind": "lesson_plan", "week": 3},
            {"title": "Indexing Strategy", "kind": "lab", "week": 5},
        ],
    },
    {
        "course_context": (
            "COURSE: Frontend Performance for Product Engineers (5 weeks)\n"
            "OBJECTIVES:\n"
            "  1. Measure Core Web Vitals on a real app.\n"
            "  2. Eliminate render-blocking resources.\n"
            "  3. Code-split and lazy-load a large bundle.\n"
            "ITEMS: Week 1 'Measuring Web Vitals' (lab), Week 2 'Critical Render "
            "Path' (lesson_plan), Week 4 'Bundle Splitting' (lab)."
        ),
        "learner_profile": {
            "experience": "product engineers, 1-3 yrs, React-fluent",
            "weekly_hours": "3",
            "goal": "make a slow production app feel fast",
        },
        "items": [
            {"title": "Measuring Web Vitals", "kind": "lab", "week": 1},
            {"title": "Critical Render Path", "kind": "lesson_plan", "week": 2},
            {"title": "Bundle Splitting", "kind": "lab", "week": 4},
        ],
    },
]


async def _capture(client) -> list[dict]:
    """Call the real engines for every case; return captured fixture records."""
    records: list[dict] = []
    for case in _CASES:
        context = case["course_context"]

        # Andragogy advisor: snapshot each advice note's free text.
        advice = await client.advise(
            course_context=context,
            learner_profile=case["learner_profile"],
            focus=None,
        )
        advice_text = "\n".join(n.text for n in advice.notes).strip()
        if advice_text:
            records.append(
                {
                    "context": context,
                    "output": advice_text,
                    "source": "advise",
                    "kind": "andragogy",
                }
            )

        # Prereq inferer: snapshot the suggestion + missing-dependency reasons.
        prereq = await client.infer(items=case["items"])
        reasons = [s.reason for s in prereq.suggested] + [
            m.reason for m in prereq.missing
        ]
        prereq_text = "\n".join(r for r in reasons if r).strip()
        if prereq_text:
            records.append(
                {
                    "context": context,
                    "output": prereq_text,
                    "source": "infer",
                    "kind": "prereq",
                }
            )
    return records


def main() -> None:
    if not settings.ANTHROPIC_API_KEY:
        print(
            "capture needs ANTHROPIC_API_KEY — skipping (no fixtures written). "
            "Set the key to snapshot real engine outputs."
        )
        sys.exit(0)

    # Import the real client lazily — only when a key is present — so this
    # module never pulls the live SDK path during the offline/skip case.
    from app.ai.client import AIClient

    client = AIClient(api_key=settings.ANTHROPIC_API_KEY)
    records = asyncio.run(_capture(client))

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(records)} real-output fixtures -> {_OUT_PATH}")
    sys.exit(0)


if __name__ == "__main__":
    main()
