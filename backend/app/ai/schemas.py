"""Pydantic v2 schemas for the SOTA-gap researcher (C2).

These models decouple the AI client from the ORM and define the structured
output contract Claude must return. Structured-output schemas must be objects
(not bare arrays), so gap findings are wrapped in ``GapReport``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CorpusDoc(BaseModel):
    """A single industry corpus document (job posting, vendor doc, etc.).

    Decouples the AI client from the ``SotaSource`` ORM row.
    """

    title: str
    kind: str
    body: str


class GapFinding(BaseModel):
    """One curriculum gap identified against the industry corpus."""

    topic: str
    coverage_status: Literal["missing", "partial", "covered"]
    # Citations: specific source titles / quotes drawn from the corpus.
    evidence: list[str] = Field(default_factory=list)
    proposed_bump: Literal["major", "minor", "patch"]
    rationale: str


class GapReport(BaseModel):
    """Wrapper object for the list of findings (structured outputs must be objects)."""

    findings: list[GapFinding] = Field(default_factory=list)


class ImpactReport(BaseModel):
    """AI estimate of a proposed curriculum change-set's impact (ADVISORY).

    The structured output of the impact analyst: a human author reads this at
    authoring time to understand how a change is likely to ripple through
    learning objectives, instructional time, and student cognitive load before
    they submit it. The field descriptions are part of the prompt — they steer
    the model under structured output, so keep them concrete.
    """

    summary: str = Field(
        description=(
            "A 2-3 sentence executive summary of the change's overall impact, "
            "written for a curriculum author skimming before they submit."
        )
    )
    learning_objectives_impact: str = Field(
        description=(
            "Narrative analysis of how the change affects the curriculum's "
            "learning objectives / outcomes: what is strengthened, weakened, "
            "added, or made obsolete, and why."
        )
    )
    affected_objectives: list[str] = Field(
        default_factory=list,
        description=(
            "The specific learning objectives, outcomes, or competencies this "
            "change touches. Be concrete; cite the objective, not a category."
        ),
    )
    duration_delta_minutes: int = Field(
        description=(
            "Estimated net change to total instructional time in MINUTES, "
            "signed: positive if the change adds teaching time, negative if it "
            "removes time, 0 if neutral."
        )
    )
    duration_rationale: str = Field(
        description=(
            "Why the duration_delta_minutes estimate is what it is — which "
            "added/changed/removed assets drive the time change."
        )
    )
    cognitive_load: Literal["lower", "unchanged", "higher", "much_higher"] = Field(
        description=(
            "Direction of the change's effect on student cognitive load: "
            "'lower' (easier), 'unchanged', 'higher', or 'much_higher' "
            "(a substantial increase in difficulty / mental effort)."
        )
    )
    cognitive_load_rationale: str = Field(
        description=(
            "Why the cognitive_load rating is what it is — cite the specific "
            "added concepts, prerequisites, or sequencing that drive it."
        )
    )
    risks: list[str] = Field(
        default_factory=list,
        description=(
            "Specific risks or watch-outs the author should weigh before "
            "submitting (e.g. broken prerequisites, overloaded weeks, "
            "assessment misalignment). Empty if none are evident."
        ),
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete, actionable recommendations to de-risk or improve the "
            "change. Empty if none apply."
        ),
    )


# --- Enrichment (placed draft proposal) ---

class StructureModule(BaseModel):
    index: int
    focus: str | None = None


class StructureProject(BaseModel):
    index: int
    title: str


class StructureAsset(BaseModel):
    key: str
    kind: str
    module_index: int | None = None


class CurriculumStructure(BaseModel):
    """Compact projection of a curriculum version — the placement target space.
    No content bodies (they live behind gs:// refs); metadata only."""
    modules: list[StructureModule] = Field(default_factory=list)
    projects: list[StructureProject] = Field(default_factory=list)
    assets: list[StructureAsset] = Field(default_factory=list)


class Placement(BaseModel):
    """Where a gap should be integrated. ADVISORY."""
    target_kind: Literal["modify_module", "new_module", "modify_asset", "new_asset"] = Field(
        description="Whether to modify an existing module/asset or add a new one."
    )
    target_ref: str | None = Field(
        default=None,
        description=(
            "For modify_module: the module INDEX as a string, taken from the provided "
            "structure. For modify_asset: the asset KEY from the provided structure. "
            "For new_module/new_asset: null. NEVER invent a ref that is not in the "
            "provided structure."
        ),
    )
    position_hint: str = Field(
        description="Human placement hint, e.g. 'after Module 5' or 'replace the Wk-2 assessment'."
    )
    rationale: str = Field(description="Why this is the right place for the gap.")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0-1.0 confidence in this placement.")


class SampleAssessment(BaseModel):
    stem: str = Field(description="The question/prompt stem.")
    kind: str = Field(description="e.g. 'mcq', 'short_answer', 'lab_task'.")
    answer_or_rubric: str = Field(description="Expected answer or a short scoring rubric.")


class DraftFrame(BaseModel):
    """A STARTER frame for the change — an outline + up to 2 sample assessments. ADVISORY."""
    outline: list[str] = Field(
        default_factory=list, description="3-7 bullet outline of the content to add or change."
    )
    sample_assessments: list[SampleAssessment] = Field(
        default_factory=list, max_length=2, description="1-2 sample assessment items. Never more than 2."
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Uncertainties or facts a human must verify. Do NOT invent benchmarks or "
            "specific numbers — surface them here instead."
        ),
    )


# --- Freshness pipeline: syllabus extraction ---

class SyllabusExtract(BaseModel):
    """Structured syllabus extraction for the freshness pipeline's university adapter."""

    course_title: str = Field(description="The course's own title as stated on the page.")
    term: str = Field(description="Semester/term if stated, else 'unknown'.")
    topics: list[str] = Field(default_factory=list, description=(
        "The distinct topics/units actually taught, one concise phrase each, in order. "
        "ONLY topics stated on the page — never infer or pad."))
    notable: list[str] = Field(default_factory=list, description=(
        "Frontier/agentic-AI-relevant highlights (agents, tool use, MCP, multi-agent, RAG, "
        "evals, observability, safety, fine-tuning) with a short why-notable each."))
    quotes: list[str] = Field(default_factory=list, description="2-4 short verbatim quotes from the page as provenance.")
    extraction_confidence: str = Field(description="'high' if a clear syllabus/schedule was present, 'medium' if partial, 'low' if the page had little curricular detail.")


# --- Freshness pipeline: net-benefit judge ---

class NetBenefitAssessment(BaseModel):
    """The Judge's verdict on one curriculum gap. ADVISORY input to a config gate."""
    evidence_strength: float = Field(ge=0, le=1, description=(
        "How strong the accumulated evidence is: number of INDEPENDENT sources, their "
        "authority (official vendor/university > news > single blog), and recency."))
    demand_signal: float = Field(ge=0, le=1, description=(
        "How strongly the evidence shows job-market / industry pull for this skill."))
    learner_value: float = Field(ge=0, le=1, description=(
        "How much adopting this would improve learner outcomes and employability."))
    curriculum_fit: float = Field(ge=0, le=1, description=(
        "How coherently this fits the curriculum's covered topics — 1.0 = natural "
        "extension of existing modules, 0.0 = alien to the curriculum's scope."))
    effort_cost: float = Field(ge=0, le=1, description=(
        "Inverse effort: 1.0 = trivial patch to an existing lesson, 0.0 = a whole new "
        "module or restructuring. HIGHER means CHEAPER (all dimensions read higher-is-better)."))
    urgency: float = Field(ge=0, le=1, description=(
        "Decay risk of NOT acting this cycle — is the field moving past the curriculum?"))
    competitive_signal: float = Field(ge=0, le=1, description=(
        "Whether the watched university curricula (university_syllabus evidence in the "
        "dossier) already teach this. Use ONLY dossier evidence — never outside knowledge."))
    recommendation: Literal["adopt_now", "monitor", "reject"] = Field(description=(
        "adopt_now: the benefit clearly outweighs cost NOW. monitor: promising but the "
        "evidence is thin — watch it. reject: noise, out of scope, or permanently low value."))
    confidence: float = Field(ge=0, le=1, description="Confidence in the recommendation.")
    rationale: str = Field(description=(
        "2-4 sentences justifying the recommendation, CITING specific dossier evidence. "
        "Never invent facts not present in the dossier."))


# --- Freshness pipeline Phase 3: generated asset content ---

class GeneratedAssetContent(BaseModel):
    """AI-generated full content for ONE asset in a proposed change_set. ADVISORY —
    it enters the existing human QA + approval + merge gates as a normal change_set."""
    content: str = Field(description=(
        "The COMPLETE new content body for the asset (full replacement, not a diff). "
        "For edits: preserve the existing structure, headings, and voice; keep unchanged "
        "sections byte-identical wherever possible so the review diff stays minimal."))
    summary_of_changes: str = Field(description=(
        "2-4 sentences for the reviewer: what changed and why, citing the gap evidence."))
    caveats: list[str] = Field(default_factory=list, description=(
        "Anything a human must verify. NEVER invent benchmarks, dates, version numbers, "
        "or statistics — surface them here instead of stating them as fact."))


# --- Authoring Platform slice 3: per-aspect generators (advisory drafts) ---
#
# The CourseAuthorAI seam extends the co-pilots from advisory to GENERATIVE.
# Each schema is a structured-output contract Claude must return; each returned
# draft is ADVISORY — the author reviews/edits/accepts it client-side, and it
# still passes through the mandatory QA -> approval -> release gate before it
# can become an active CurriculumVersion. Field descriptions steer the model
# and carry the freshness-pipeline safety discipline: never invent
# facts/citations, surface uncertainty in caveats.

class GeneratedObjective(BaseModel):
    """One AI-drafted, Bloom-tagged learning objective. ADVISORY — the author edits/accepts it."""
    text: str = Field(description=(
        "A single, measurable learning objective phrased from the learner's perspective "
        "(e.g. 'Explain how ...', 'Build a ...'). Start with an observable Bloom verb; "
        "keep it to one outcome, concrete and assessable — not a topic label."))
    bloom_level: Literal[
        "remember", "understand", "apply", "analyze", "evaluate", "create"
    ] = Field(description=(
        "The Bloom's-taxonomy cognitive level the objective targets. It MUST match the "
        "verb used in `text` — 'apply' for a build/use objective, 'analyze' for a "
        "compare/diagnose objective, and so on."))
    key_skills: list[str] = Field(default_factory=list, description=(
        "2-5 concrete skills or tools the objective develops (short noun phrases). "
        "Ground these in the topic/learner profile provided; do NOT invent tools that "
        "the topic does not imply."))


class GeneratedObjectives(BaseModel):
    """A set of AI-drafted objectives for a topic. Wrapper because structured output
    must be an object, not a bare array. ADVISORY — the author curates the list."""
    objectives: list[GeneratedObjective] = Field(description=(
        "The drafted learning objectives, ordered from foundational to advanced. "
        "Return only genuinely distinct objectives grounded in the requested topic "
        "and learner profile — quality and progression over padding the count."))


class GeneratedItemContent(BaseModel):
    """AI-generated body for ONE draft content item (lesson_plan / lab / spec / ...).
    ADVISORY — the author edits/accepts it; it never auto-writes into the draft."""
    kind: str = Field(description=(
        "Echo of the asset kind this content was written for (e.g. 'lesson_plan', "
        "'lab', 'spec', 'slides'). MUST equal the kind requested — the body's shape "
        "and voice follow the kind."))
    content_markdown: str = Field(description=(
        "The COMPLETE item body in Markdown, written to teach the target objective at "
        "its Bloom level and consistent with the surrounding course context. Shape it "
        "to the asset kind (a lab has setup + steps + checks; a lesson_plan has "
        "sections + activities). Do not include front-matter or a course-level title."))
    summary: str = Field(description=(
        "1-3 sentences telling the author what this draft covers and how it serves the "
        "objective, so they can decide whether to accept or revise it."))
    caveats: list[str] = Field(default_factory=list, description=(
        "Anything a human must verify before publishing. NEVER invent benchmarks, dates, "
        "versions, statistics, or citations — surface them here instead of asserting them "
        "as fact, and note anything you were uncertain about."))


class DeckDiagramSpec(BaseModel):
    """One structural diagram the deck references. ADVISORY — a human renders it.

    The standard's diagram pipeline is: a Mermaid ``.mmd`` source is rendered to a
    landscape PNG that the deck embeds. A generated deck REFERENCES each diagram as
    a Markdown image ``![alt](../diagrams/{filename}.png)`` in ``deck_markdown`` and
    carries the matching Mermaid source HERE, so the controller renders the source
    to that PNG (``render_deck(deck_md, diagrams={filename: mermaid})``, e.g. via
    ``app.slides.from_generated.diagrams_from_specs``). The deck itself must NOT
    inline a fenced ``mermaid`` block — Marp renders that as raw code, not a
    diagram. The model must NOT invent a diagram for a concept that is not
    structural."""

    filename: str = Field(description=(
        "The diagram's file stem, e.g. 'agent_loop' (no extension). The deck should "
        "reference it as '../diagrams/{filename}.png' where a rendered PNG is used, "
        "and the corresponding source is '../diagrams/{filename}.mmd'."))
    mermaid: str = Field(description=(
        "The complete Mermaid source for this diagram. Prefer 'flowchart LR' for a "
        "landscape shape (portrait diagrams downscale and become illegible). Ground it "
        "in the concept it illustrates — do NOT invent structure the content does not imply."))
    alt_text: str = Field(description=(
        "A concrete alt-text description of the diagram for accessibility and for the "
        "speaker notes' ALT TEXT line — describe what the diagram shows, not just its title."))


class GeneratedDeck(BaseModel):
    """AI-generated Marp ``deck.md`` for one course/module. ADVISORY (D-2): the deck
    is fully AI-generated but a HUMAN reviews it before release — it is returned for
    review and never auto-published.

    NOTE (public mirror): the production model targets a proprietary house slide
    standard (a fixed narrative arc, a locked theme, a callout vocabulary); that
    standard is withheld here and these descriptions are generic. Real visual
    quality is only proven once the deck is rendered via the S1 pipeline
    (Marp -> PDF/PPTX/HTML); CI validates STRUCTURE + grounding, not pixels."""

    deck_markdown: str = Field(description=(
        "The COMPLETE Marp deck source (a valid 'deck.md'): YAML front-matter with "
        "'marp: true' and a theme, '---' slide breaks between slides, fenced code and "
        "tables, and '<!-- _notes: ... -->' speaker notes. For a structural concept use a "
        "Markdown image reference '![alt](../diagrams/<stem>.png)' — NEVER an inline "
        "```mermaid``` fenced block (Marp renders that as raw code, not a diagram). Ground "
        "EVERY slide strictly in the supplied objectives and content."))
    diagram_specs: list[DeckDiagramSpec] = Field(default_factory=list, description=(
        "One entry per structural diagram the deck references — each carries the Mermaid "
        "source that is rendered to the '../diagrams/<filename>.png' the deck's image ref "
        "points at (filename == the image ref's stem). Empty is valid for a deck with no "
        "structural diagram, but most decks have at least one (the worked-example "
        "loop/architecture)."))
    summary: str = Field(description=(
        "2-4 sentences for the reviewing human: what the deck covers, how many slides, and "
        "which concepts it emphasizes, so they can decide whether to render and ship it."))
    caveats: list[str] = Field(default_factory=list, description=(
        "Anything a human must verify before release: any citation/benchmark/date the source "
        "content did not supply, any concept where grounding was thin, and the standing caveat "
        "that visual fit is unverified until rendered via S1. NEVER invent facts to fill a gap — "
        "surface the gap here instead."))


class GeneratedAssessment(BaseModel):
    """AI-generated assessment + rubric for one objective. ADVISORY — the author
    edits/accepts it; it still passes the mandatory QA gate before release."""
    content_markdown: str = Field(description=(
        "The COMPLETE assessment in Markdown — the questions/tasks that measure whether "
        "the learner has met the objective at its Bloom level. Include an answer key or "
        "expected-response notes for the instructor."))
    rubric: str = Field(description=(
        "A scoring rubric in Markdown: the criteria and the performance levels (what "
        "earns full vs partial vs no credit). It must align with what the assessment "
        "actually asks and with the objective being measured."))
    caveats: list[str] = Field(default_factory=list, description=(
        "Anything a human must verify before use. NEVER invent facts, statistics, or "
        "citations — surface uncertainty here rather than stating it as fact."))
