"""AI builder co-pilot engines for the Course Builder (Phase 2).

Two advisory engines — a Categorizer that classifies a draft item into an
asset kind + estimates effort, and an AndragogyAdvisor that produces Knowles-
grounded improvement notes for a draft course. Both are ADVISORY: a human
author reviews and decides; the models never mutate state.

Design mirrors ``impact.py`` exactly:
  - Each engine is a ``@runtime_checkable Protocol`` seam. The real
    ``AIClient`` implements both; tests inject fakes — ZERO real API calls
    in CI.
  - This module does NOT import ``client.py`` (avoids circular imports).
    ``client.py`` imports the Protocols + prompt builders + output models
    from here.
  - User-supplied free text is length-capped by ``_clip`` before it enters
    any prompt (defense-in-depth against prompt injection).
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

# Re-expose _clip so callers can import it; identical to impact.py's version
# but kept local to avoid a cross-module dependency inside the ``ai`` package.

def _clip(value: object, limit: int = 200) -> str:
    """Length-cap a user-controlled string before interpolation into a prompt.

    Defense-in-depth against prompt injection: ``title``, ``content``, free
    text from the author — all go through ``_clip`` before entering the user
    message. The output is further constrained by structured Pydantic
    validation, and the result is advisory — but limiting the injection
    surface is a cheap, layered defence.
    """
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


# ---------------------------------------------------------------------------
# Categorizer — classify a draft item into an asset kind + estimate effort
# ---------------------------------------------------------------------------

_VALID_ASSET_KINDS = (
    "lesson_plan", "slides", "assessment", "rubric", "lab",
    "spec", "starter", "references", "learning_objectives", "project",
)

# Literal type derived from the same tuple so the two stay in sync.
AssetKindStr = Literal[
    "lesson_plan", "slides", "assessment", "rubric", "lab",
    "spec", "starter", "references", "learning_objectives", "project",
]


class CategorizeResult(BaseModel):
    """Structured output returned by the Categorizer.

    ``kind`` must be one of the canonical AssetKind values (validated by
    Pydantic via a Literal — out-of-vocabulary model responses are rejected at
    parse time). ``estimated_minutes`` is the student-facing time budget (not
    instructor prep). ``complexity`` is a relative multiplier anchored at 1.0
    (average); >1 means denser/harder, <1 means lighter.
    """

    kind: Annotated[
        AssetKindStr,
        Field(
            description=(
                "The best-fit asset kind for this item. Must be one of: "
                + ", ".join(_VALID_ASSET_KINDS)
                + ". Choose the kind that most accurately reflects the primary "
                "instructional activity a student performs."
            )
        ),
    ]
    served_objective_hint: Annotated[
        str,
        Field(
            description=(
                "A short phrase (≤ 120 characters) identifying which of the "
                "provided learning objectives this item most directly serves. "
                "If no objectives were provided, describe the probable learning "
                "outcome the item supports."
            )
        ),
    ]
    estimated_minutes: Annotated[
        int,
        Field(
            gt=0,
            description=(
                "Student-facing time budget in minutes — how long a typical "
                "learner will need to engage with this item (NOT instructor "
                "prep time). Be realistic: a dense 20-slide deck is not 20 "
                "minutes."
            ),
        ),
    ]
    complexity: Annotated[
        float,
        Field(
            ge=0.7,
            le=1.5,
            description=(
                "Relative cognitive-load multiplier anchored at 1.0 (average "
                "difficulty for the target audience). Use >1.0 for denser or "
                "harder material and <1.0 for lighter review or introductory "
                "content. Stay within [0.7, 1.5]."
            ),
        ),
    ]
    rationale: Annotated[
        str,
        Field(
            description=(
                "One-to-two sentence explanation of why you chose this kind, "
                "estimated duration, and complexity multiplier. Ground the "
                "claim in specific signals from the title or content."
            )
        ),
    ]


@runtime_checkable
class Categorizer(Protocol):
    """Anything that can classify a draft item into a CategorizeResult.

    The real ``AIClient`` implements this; tests inject a ``FakeCategorizer``.
    """

    async def categorize(
        self,
        *,
        title: str,
        content: str | None,
        objectives: list[str],
    ) -> CategorizeResult: ...


# System prompt for the Categorizer — grounded and explicitly advisory. The
# author accepts or edits; the model never writes to the DB.
# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# below preserves the task + the structured-output contract (the closed asset-
# kind list, the [0.7, 1.5] complexity band) and the grounding/advisory rules;
# the production calibration guidance is not shipped here.
CATEGORIZE_SYSTEM_PROMPT = (
    "You are a curriculum-design assistant. Given a draft item (title + optional "
    "content excerpt) and the course's learning objectives, classify the item "
    "into the best-fit asset kind from this closed list: "
    + ", ".join(_VALID_ASSET_KINDS)
    + "; name the objective it best serves; estimate the student-facing minutes "
    "(not instructor prep); assign a complexity multiplier in [0.7, 1.5] anchored "
    "at 1.0; and write a short rationale citing specific signals.\n\n"
    "Ground every field in the actual title/content provided — do NOT invent "
    "content that is absent. This classification is ADVISORY ONLY: the author "
    "reviews and decides; never assume your output is applied automatically."
)


def build_categorize_prompt(
    *,
    title: str,
    content: str | None,
    objectives: list[str],
) -> str:
    """Build the user message for the Categorizer.

    All author-supplied free text is length-capped via ``_clip`` as a
    prompt-injection mitigation before it enters the prompt.
    """
    parts: list[str] = [f"ITEM TITLE:\n{_clip(title, 300)}"]

    if content:
        parts.append(f"CONTENT EXCERPT:\n{_clip(content, 1000)}")
    else:
        parts.append("CONTENT EXCERPT:\n(none provided)")

    if objectives:
        obj_list = "\n".join(
            f"  {i + 1}. {_clip(o, 200)}" for i, o in enumerate(objectives)
        )
        parts.append(f"COURSE OBJECTIVES:\n{obj_list}")
    else:
        parts.append("COURSE OBJECTIVES:\n(none provided)")

    parts.append(
        "Classify this item, estimate student-facing minutes, assign a "
        "complexity multiplier, and identify the objective it best serves."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# AndragogyAdvisor — Knowles-grounded adult-learning advisor
# ---------------------------------------------------------------------------


class AdviceItem(BaseModel):
    """A single advisory note from the andragogy advisor.

    ``kind`` is the note type: suggestion (concrete improvement),
    question (Socratic probe for the author), or warning (risk/gap).
    ``target_kind`` anchors the note to the course as a whole, a specific
    week, or a specific item. ``target_ref`` carries the week number or
    item title when target_kind is "week" or "item".
    """

    kind: Literal["suggestion", "question", "warning"]
    text: str
    target_kind: Literal["item", "week", "course"]
    target_ref: str | None = None


class AdviceReport(BaseModel):
    """Structured output returned by the AndragogyAdvisor."""

    notes: list[AdviceItem]


@runtime_checkable
class AndragogyAdvisor(Protocol):
    """Anything that can turn (course context + learner profile) into AdviceReport.

    The real ``AIClient`` implements this; tests inject a ``FakeAdvisor``.
    """

    async def advise(
        self,
        *,
        course_context: str,
        learner_profile: dict,
        focus: str | None = None,
    ) -> AdviceReport: ...


# System prompt for the AndragogyAdvisor — high-signal, explicitly advisory.
# NOTE (public mirror): proprietary rubric withheld. Production applies a
# specific, named set of adult-learning diagnostic lenses to each note; that
# rubric is not shipped here. The generic instruction below preserves the
# structured task (a mix of suggestions/questions/warnings, each anchored to
# course/week/item) and the grounding/advisory discipline.
ANDRAGOGY_SYSTEM_PROMPT = (
    "You are a curriculum-design advisor for adult learners. Review a draft "
    "course against sound adult-learning practice and return a SHORT, HIGH-SIGNAL "
    "set of 3–7 notes, tailored to the learner profile provided.\n\n"
    "  - Produce a MIX of concrete suggestions (actionable changes), Socratic "
    "questions (prompts that help the author think), and warnings (gaps/risks).\n"
    "  - Anchor each note to where it applies: the full course, a specific week, "
    "or a specific item. Do not pad with generic advice.\n\n"
    "Rules:\n"
    "  - Every note must be grounded in a concrete signal from the course "
    "context. Do not invent content that is not described.\n"
    "  - This report is ADVISORY ONLY. A human author reviews it and decides "
    "what to act on. Never assume your advice is automatically applied."
)


def build_advise_prompt(
    *,
    course_context: str,
    learner_profile: dict,
    focus: str | None = None,
) -> str:
    """Build the user message for the AndragogyAdvisor.

    ``course_context`` is a structured summary built by the router; it may be
    long, so we clip it at a generous limit (4000 chars) to bound token spend
    while keeping enough signal. Learner profile values and the optional focus
    string are shorter and clipped individually.
    """
    # course_context is system-generated (router-built), but we still clip it
    # to bound accidental blowout; focus is author free text — tighter cap.
    clipped_context = _clip(course_context, 4000)
    clipped_focus = _clip(focus, 400) if focus else None

    # Render the learner profile as a compact key: value block.
    if learner_profile:
        profile_lines = "\n".join(
            f"  {_clip(k, 40)}: {_clip(v, 200)}"
            for k, v in learner_profile.items()
            if v is not None
        )
        profile_block = profile_lines or "  (no details provided)"
    else:
        profile_block = "  (no learner profile provided)"

    parts: list[str] = [
        f"COURSE CONTEXT:\n{clipped_context}",
        f"LEARNER PROFILE:\n{profile_block}",
    ]
    if clipped_focus:
        parts.append(f"FOCUS AREA:\n{clipped_focus}")

    parts.append(
        "Apply the six andragogy principles as diagnostic lenses and produce "
        "3–7 high-signal advisory notes (mix of suggestions, questions, and "
        "warnings). Anchor each note to the course, a specific week, or a "
        "specific item. Be concise and actionable."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# PrereqInferer — suggest prerequisite edges + flag missing dependencies
# ---------------------------------------------------------------------------


class PrereqSuggestion(BaseModel):
    """A single suggested prerequisite edge between two course items.

    Semantics: ``from_title`` is the PREREQUISITE that must come BEFORE
    ``to_title``.  The edge direction is from_title → to_title, meaning
    "from_title is a prerequisite of to_title".  When persisted this maps
    directly to ``DraftDependency(from_item_id=<from>, to_item_id=<to>)``.
    """

    from_title: Annotated[
        str,
        Field(
            description=(
                "Title of the item that is the PREREQUISITE — the item a learner "
                "must complete BEFORE they tackle 'to_title'. This is the source "
                "of the edge: from_title → to_title."
            )
        ),
    ]
    to_title: Annotated[
        str,
        Field(
            description=(
                "Title of the item that DEPENDS ON 'from_title'. A learner should "
                "complete 'from_title' before starting this item."
            )
        ),
    ]
    reason: Annotated[
        str,
        Field(
            description=(
                "One-sentence explanation of why from_title is a prerequisite of "
                "to_title, grounded in concrete concepts or skills."
            )
        ),
    ]


class MissingDependency(BaseModel):
    """An item that uses a concept or skill not taught by any earlier item."""

    item_title: Annotated[
        str,
        Field(
            description=(
                "Exact title of the item that uses a concept or skill that is not "
                "taught by any earlier item in the course."
            )
        ),
    ]
    needs: Annotated[
        str,
        Field(
            description=(
                "Short name of the missing concept, skill, or topic the item depends "
                "on (≤ 80 characters)."
            )
        ),
    ]
    reason: Annotated[
        str,
        Field(
            description=(
                "One sentence explaining why this concept or skill is needed and why "
                "it is not covered by any earlier item."
            )
        ),
    ]


class PrereqReport(BaseModel):
    """Structured output returned by the PrereqInferer."""

    suggested: list[PrereqSuggestion]
    missing: list[MissingDependency]


@runtime_checkable
class PrereqInferer(Protocol):
    """Anything that can turn a list of course items into a PrereqReport.

    Each item dict has at least ``title`` (str), ``kind`` (str), and
    ``week`` (int | None).  The real ``AIClient`` implements this; tests
    inject a ``FakePrereqInferer`` — ZERO real API calls in CI.
    """

    async def infer(self, *, items: list[dict]) -> PrereqReport: ...


# System prompt for the PrereqInferer — precision over recall, explicitly
# advisory. The edge-direction convention (from_title → to_title) is the schema
# contract and is stated here so output maps cleanly onto ``DraftDependency``.
# NOTE (public mirror): proprietary rubric withheld — the production dependency-
# analysis heuristics are not shipped. The generic instruction below preserves
# the two-task output contract, the edge direction, and the grounding rules.
PREREQ_SYSTEM_PROMPT = (
    "You analyse the items in a draft course and propose its prerequisite "
    "structure. A 'suggested' edge is from_title → to_title, meaning from_title "
    "is the PREREQUISITE (taught first) and to_title DEPENDS on it (taught "
    "after).\n\n"
    "Two tasks:\n"
    "  1. SUGGEST prerequisite edges (from_title, to_title) only where there is a "
    "clear, pedagogically necessary dependency. Do NOT propose both directions "
    "for a pair.\n"
    "  2. FLAG missing dependencies — items that use a concept or skill not "
    "introduced by any earlier item.\n\n"
    "Rules:\n"
    "  - Only reference item titles that appear EXACTLY in the provided list; do "
    "NOT invent items, concepts, or titles.\n"
    "  - Precision over recall: a small, high-confidence set beats a long "
    "speculative one.\n"
    "  - This report is ADVISORY ONLY. A human author reviews every suggestion "
    "and decides. Never assume your output is applied automatically."
)


def build_prereq_prompt(*, items: list[dict]) -> str:
    """Build the user message for the PrereqInferer.

    Renders the item list compactly (title clipped to 200 chars, kind, week).
    All author-controlled strings go through ``_clip`` as a prompt-injection
    mitigation.
    """
    if not items:
        item_block = "  (no items)"
    else:
        item_lines = []
        for item in items:
            title = _clip(item.get("title", ""), 200)
            kind = _clip(item.get("kind", ""), 40)
            week = item.get("week")
            week_str = f"week {week}" if week is not None else "unscheduled"
            item_lines.append(f"  - {title} [{kind}] ({week_str})")
        item_block = "\n".join(item_lines)

    return (
        f"COURSE ITEMS:\n{item_block}\n\n"
        "Analyse the items above and return:\n"
        "  1. suggested — prerequisite edges (from_title → to_title) a learner "
        "needs; keep to high-confidence pairs only.\n"
        "  2. missing — items that depend on a concept or skill not covered by "
        "any earlier item (gaps in the course structure)."
    )
