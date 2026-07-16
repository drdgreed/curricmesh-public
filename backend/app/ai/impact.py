"""AI CCR-impact guidance — estimate a change-set's impact (Milestone B).

At authoring time Claude estimates how a proposed curriculum change-set will
ripple through learning objectives, instructional duration, and student
cognitive load — BEFORE the author submits. This is ADVISORY: a human decides.

Design (mirrors ``qa_judge`` / ``client`` exactly):
  - ``ImpactAnalyzer`` is the seam the router depends on. The real ``AIClient``
    implements it; tests inject a fake — ZERO real Anthropic calls in CI.
  - ``score_impact`` is the thin orchestration helper: it builds the user
    message from the change-set (+ optional title/rationale + light curriculum
    context) and delegates to the analyzer. No DB writes here; the router owns
    persistence + the transaction boundary.
  - API / structured-output errors propagate — never swallowed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ai.schemas import ImpactReport
from app.schemas.release import ReleaseChangeSet

# The impact analyst's system prompt. Specific, change-grounded, and explicitly
# advisory — the model must never assume its estimate ships a change.
IMPACT_SYSTEM_PROMPT = (
    "You are a curriculum-design impact analyst. Given a curriculum context and "
    "a proposed change-set, estimate the impact on (1) learning objectives, "
    "(2) instructional duration, and (3) student cognitive load.\n\n"
    "Rules:\n"
    "- Be specific and cite the change: ground every claim in a concrete "
    "added / changed / removed asset or edge from the change-set. Do NOT invent "
    "assets that are not in the change-set.\n"
    "- duration_delta_minutes is a SIGNED estimate of the net change to total "
    "instructional time in minutes (+ adds time, - removes time, 0 neutral).\n"
    "- cognitive_load is the DIRECTION of the effect on student mental effort: "
    "'lower', 'unchanged', 'higher', or 'much_higher'.\n"
    "- Surface real risks (broken prerequisites, overloaded weeks, assessment "
    "misalignment) and actionable recommendations; leave a list empty if none "
    "apply rather than padding it.\n"
    "- This assessment is ADVISORY ONLY. A human author reviews it and decides "
    "whether to submit, edit, or discard the change. Never assume your analysis "
    "ships a change."
)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


@runtime_checkable
class ImpactAnalyzer(Protocol):
    """Anything that can turn (change-set + context) into an ImpactReport."""

    async def analyze_impact(
        self,
        *,
        change_set: ReleaseChangeSet,
        title: str | None = None,
        rationale: str | None = None,
        context: str | None = None,
    ) -> ImpactReport: ...


# ---------------------------------------------------------------------------
# User-message construction
# ---------------------------------------------------------------------------


def _clip(value: object, limit: int = 200) -> str:
    """Length-cap a user-controlled string before it goes into the prompt.

    Defense-in-depth against prompt injection: ``lineage_key`` / ``section`` /
    ``title`` / ``rationale`` are author-supplied free text interpolated into the
    analyst's user message. Capping their length bounds how much an attacker can
    inject. (The output is still constrained by structured ``ImpactReport``
    validation, and the analysis is advisory — but this limits the surface.)
    """
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _format_change_set(change_set: ReleaseChangeSet) -> str:
    """Render the change-set as a compact, model-readable summary.

    Lists added / changed / removed assets by ``lineage_key`` (+ kind) and the
    edge deltas, plus the proposed bump — exactly the signal the analyst needs
    to ground its estimate. User-controlled strings are length-capped (``_clip``)
    as a prompt-injection mitigation.
    """
    added = "\n".join(
        f"  - {_clip(a.lineage_key)} (kind: {a.kind.value if hasattr(a.kind, 'value') else a.kind}"
        f", week {a.week_index}, section {_clip(a.section, 120)!r})"
        for a in change_set.added
    ) or "  (none)"
    changed = "\n".join(
        f"  - {_clip(c.lineage_key)}"
        + (" [content edited]" if c.content is not None else "")
        + (" [moved]" if (c.section or c.week_index is not None or c.order is not None) else "")
        for c in change_set.changed
    ) or "  (none)"
    removed = "\n".join(f"  - {_clip(k)}" for k in change_set.removed) or "  (none)"
    edges_added = "\n".join(
        f"  - {_clip(e.from_key)} -> {_clip(e.to_key)} ({_clip(e.edge_type, 40)})"
        for e in change_set.edges_added
    ) or "  (none)"
    edges_removed = "\n".join(
        f"  - {_clip(e.from_key)} -> {_clip(e.to_key)} ({_clip(e.edge_type, 40)})"
        for e in change_set.edges_removed
    ) or "  (none)"

    return (
        f"PROPOSED BUMP: {change_set.bump}\n\n"
        f"ADDED ASSETS:\n{added}\n\n"
        f"CHANGED ASSETS:\n{changed}\n\n"
        f"REMOVED ASSETS:\n{removed}\n\n"
        f"PREREQUISITE EDGES ADDED:\n{edges_added}\n\n"
        f"PREREQUISITE EDGES REMOVED:\n{edges_removed}"
    )


def build_user_prompt(
    *,
    change_set: ReleaseChangeSet,
    title: str | None = None,
    rationale: str | None = None,
    context: str | None = None,
) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"CHANGE TITLE:\n{_clip(title, 300)}")
    if rationale:
        parts.append(f"AUTHOR RATIONALE:\n{_clip(rationale, 1000)}")
    if context:
        parts.append(f"CURRICULUM CONTEXT (current active version):\n{context}")
    parts.append("PROPOSED CHANGE-SET:\n" + _format_change_set(change_set))
    parts.append(
        "Estimate the impact on learning objectives, instructional duration, and "
        "student cognitive load. Cite the change."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def score_impact(
    *,
    analyzer: ImpactAnalyzer,
    change_set: ReleaseChangeSet,
    title: str | None = None,
    rationale: str | None = None,
    context: str | None = None,
) -> ImpactReport:
    """Estimate the impact of a change-set. No DB writes — the router persists.

    Lets analyzer (API) errors and structured-output errors propagate — never
    returns a silently-partial report.
    """
    return await analyzer.analyze_impact(
        change_set=change_set,
        title=title,
        rationale=rationale,
        context=context,
    )
