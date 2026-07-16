"""Anthropic wrapper for the SOTA-gap researcher (C2).

Design:
  - ``GapExtractor`` is the seam the rest of the app depends on. The real
    ``AIClient`` implements it; tests inject a fake. This keeps ZERO real API
    calls out of CI.
  - ``anthropic`` is imported at module level (it's a declared dependency, so
    importing this module never fails), but the real ``anthropic.AsyncAnthropic``
    client is built LAZILY on first use — importing this module during test
    collection cannot fail on a missing API key.
  - API errors are NOT swallowed; they propagate to the caller.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Protocol, runtime_checkable

import anthropic
from pydantic import BaseModel

from app.ai.builder_advisor import (
    ANDRAGOGY_SYSTEM_PROMPT,
    CATEGORIZE_SYSTEM_PROMPT,
    PREREQ_SYSTEM_PROMPT,
    AdviceReport,
    AndragogyAdvisor,
    CategorizeResult,
    Categorizer,
    PrereqInferer,
    PrereqReport,
    build_advise_prompt,
    build_categorize_prompt,
    build_prereq_prompt,
)
from app.ai.impact import IMPACT_SYSTEM_PROMPT, ImpactAnalyzer, build_user_prompt
from app.ai.observability import usage
from app.ai import usage_store
from app.ai.qa_judge import QAJudge, QAJudgement
from app.ai.deckgen import (
    DECK_SYSTEM_PROMPT,
    DeckGenerator,
    build_deck_user_prompt,
)
from app.ai.schemas import (
    CorpusDoc, CurriculumStructure, DraftFrame, GapFinding, GapReport, GeneratedAssetContent,
    GeneratedAssessment, GeneratedDeck, GeneratedItemContent, GeneratedObjectives,
    ImpactReport, NetBenefitAssessment, Placement, SyllabusExtract,
)
from app.ai.tutor import (
    ASSESS_SYSTEM_PROMPT,
    COACH_SYSTEM_PROMPT,
    TUTOR_SYSTEM_PROMPT,
    AssessmentEvaluation,
    CoachMessage,
    Tutor,
    TutorAnswer,
    build_assess_user_prompt,
    build_coach_user_prompt,
    build_tutor_user_prompt,
)
from app.config import settings
from app.core.workflow.rules import QA_DIMENSIONS
from app.schemas.release import ReleaseChangeSet

logger = logging.getLogger(__name__)

# opus-4-8 supports structured outputs + adaptive thinking. Do NOT pass
# temperature/top_p/budget_tokens — they 400 on this model.
_MODEL = "claude-opus-4-8"
# The model a refused (Fable-5) call falls back to: Opus 4.8 does not refuse
# via stop_reason and is our reliable baseline.
_FALLBACK_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 8000

# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# below preserves the task + the structured-output contract (the coverage_status
# and proposed_bump enum values) and the "never invent, cite evidence" rule.
_SYSTEM_PROMPT = (
    "You are a curriculum SOTA-gap analyst. Given (1) the topics a curriculum "
    "currently covers and (2) an industry corpus of job postings and vendor "
    "documentation, identify topics in-demand in the corpus but MISSING or "
    "UNDER-COVERED in the curriculum.\n\n"
    "Rules:\n"
    "- Report ONLY genuine gaps and cite the specific corpus evidence (source "
    "title or a short quote) for each. Do NOT invent topics absent from the "
    "corpus. Precision matters more than recall.\n"
    "- coverage_status is one of: 'missing', 'partial', or 'covered'.\n"
    "- proposed_bump ('major' / 'minor' / 'patch') reflects the size of the "
    "curriculum change the gap would require.\n"
)

# Appended to _SYSTEM_PROMPT when content cards are provided. The module-level
# constant is NEVER mutated — extract_gaps builds a fresh string each call.
# NOTE (public mirror): proprietary rubric withheld — generic content-over-titles
# instruction retained so the seam still switches on content-aware judging.
_CONTENT_AWARE_SYSTEM_SUFFIX = (
    "\nWhen content cards are provided, judge coverage from what the content "
    "actually teaches rather than from titles alone."
)


# QA-reviewer system prompt for the LLM-as-judge (C3). Scores a CCR on the six
# canonical dimensions (1–5 + evidence), strict/calibrated, ADVISORY only.
# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# below preserves the structured contract — the six canonical dimension names,
# the 1–5 scale, exactly-one-judgement-per-dimension — and the content-grounded,
# advisory-only discipline; the per-dimension scoring rubric is not shipped.
_QA_SYSTEM_PROMPT = (
    "You are a strict, calibrated curriculum QA reviewer. Given a Change Control "
    "Request (CCR) — title, rationale, proposed version bump, and proposed "
    "changes — score the change on EACH of these six dimensions on an integer "
    "scale of 1 (poor) to 5 (excellent), citing one-to-two sentences of concrete "
    "evidence from the CCR for each: content_accuracy, alignment, prerequisites, "
    "consistency, instructor_support, student_experience.\n\n"
    "Rules:\n"
    "- Return EXACTLY one judgement per dimension above — no missing, extra, or "
    "duplicate dimensions. Use the exact dimension names given.\n"
    "- Be strict and calibrated: reserve 5 for genuinely excellent work; score "
    "low when evidence is thin or the proposal is underspecified.\n"
    "- When a 'PROPOSED CONTENT' section with the actual asset bodies is "
    "provided, judge from that content itself, not the title/impact summary "
    "alone. Long bodies may be truncated (flagged inline); score what you see.\n"
    "- This assessment is ADVISORY ONLY. A human QA Lead makes the final call "
    "and may accept, edit, or override your scores. Never assume your review "
    "ships a change."
)


@runtime_checkable
class GapExtractor(Protocol):
    """The seam: anything that can turn (covered topics, corpus) into findings."""

    async def extract_gaps(
        self,
        covered_topics: list[str],
        corpus_docs: list[CorpusDoc],
        covered_content: list[dict] | None = None,
    ) -> list[GapFinding]: ...


@runtime_checkable
class GapEnricher(Protocol):
    """The seam: attaches a placement + draft frame to a detected-gap CCR."""

    async def place_gap(
        self, finding: GapFinding, structure: CurriculumStructure
    ) -> Placement: ...

    async def draft_frame(
        self, finding: GapFinding, placement: Placement
    ) -> DraftFrame: ...


@runtime_checkable
class SyllabusExtractor(Protocol):
    """The seam: turns raw university course page text into a structured syllabus extract."""

    async def extract_syllabus(
        self, page_text: str, context: str
    ) -> SyllabusExtract: ...


@runtime_checkable
class GapJudge(Protocol):
    """The seam: judges a detected gap and returns a net-benefit assessment."""

    async def judge_gap(
        self, finding: GapFinding, covered_topics: list[str], dossier: list[dict]
    ) -> NetBenefitAssessment: ...


@runtime_checkable
class ContentGenerator(Protocol):
    """The seam: generates full asset content for one target in a proposed change_set."""

    async def generate_asset_content(
        self, *, mode: str, current_content: str | None, draft_frame: dict,
        dossier: list[dict], style_samples: list[str], asset_kind: str, topic: str,
    ) -> "GeneratedAssetContent": ...


@runtime_checkable
class CourseAuthorAI(Protocol):
    """The author-time GENERATIVE seam (Authoring Platform slice 3).

    Extends the co-pilots from advisory to generative: per-aspect generators
    that produce editable DRAFTS. Every method returns an advisory draft the
    author accepts/edits — nothing is auto-applied, and the mandatory
    QA -> approval -> release gate still stands between a draft and an active
    version. The real ``AIClient`` implements this; tests inject a fake.
    """

    async def generate_objectives(
        self, *, topic: str, learner_profile: dict, count: int = 5,
        language: str = "en",
    ) -> "GeneratedObjectives": ...

    async def generate_item_content(
        self, *, objective: str, kind: str, course_context: str,
        language: str = "en",
    ) -> "GeneratedItemContent": ...

    async def generate_assessment(
        self, *, objective: str, course_context: str, language: str = "en",
    ) -> "GeneratedAssessment": ...

    async def generate_deck(
        self, *, module_title: str, module_number: str, module_id: str,
        objectives: list[dict], items: list[dict], bloom_ceiling: str | None = None,
        language: str = "en",
    ) -> "GeneratedDeck": ...


@runtime_checkable
class WebSearcher(Protocol):
    """The seam for the LIVE corpus: gathers current field signal from the web.

    The real ``AIClient`` implements this via Anthropic's web_search server tool;
    tests inject a fake that returns canned ``CorpusDoc``s — ZERO network in CI.
    """

    async def web_search_corpus(
        self, query: str, max_results: int
    ) -> list[CorpusDoc]: ...


# The live web-search system/user prompts steer Claude to gather CURRENT
# (2026+) industry/job-market skill expectations for the curriculum's domain —
# this is the live field signal that the curated snapshot can't capture.
# NOTE (public mirror): proprietary rubric withheld — generic web-search brief
# retained so the live-corpus seam stays runnable.
_WEB_SEARCH_SYSTEM_PROMPT = (
    "You are a labor-market research assistant for curriculum design. Given a "
    "curriculum domain, use web search to gather the CURRENT (2026 and later) "
    "industry and job-market expectations for it: the concrete skills, tools, "
    "frameworks, and topics employers are hiring for now. Prefer recent job "
    "postings, vendor documentation, and reputable surveys, and cite specific "
    "sources. Breadth of distinct, current, in-demand skills matters more than "
    "depth on any one source."
)


def _build_user_prompt(
    covered_topics: list[str],
    corpus_docs: list[CorpusDoc],
    covered_content: list[dict] | None = None,
) -> str:
    covered = "\n".join(f"- {t}" for t in covered_topics) or "(none)"
    docs = "\n\n".join(
        f"### Source: {d.title} (kind: {d.kind})\n{d.body}" for d in corpus_docs
    ) or "(empty corpus)"
    base = (
        "TOPICS THE CURRICULUM CURRENTLY COVERS:\n"
        f"{covered}\n\n"
        "INDUSTRY CORPUS:\n"
        f"{docs}\n\n"
        "Return the gap findings."
    )
    if covered_content is None:
        return base
    return base + "\n\nCURRICULUM CONTENT CARDS:\n" + json.dumps(covered_content, indent=2)


# NOTE (public mirror): proprietary rubric withheld — generic placement brief;
# the target-selection contract (pick only from the provided structure) is kept.
_PLACEMENT_SYSTEM_PROMPT = (
    "You place a curriculum gap into an existing curriculum. Choose the single best "
    "integration point. You MUST pick a target only from the provided structure (a module "
    "index or an asset key), or return a new_module/new_asset with target_ref=null. NEVER "
    "invent a module index or asset key that is not in the structure."
)

# NOTE (public mirror): proprietary rubric withheld — generic starter-frame brief.
_DRAFT_SYSTEM_PROMPT = (
    "You draft a STARTER frame for a curriculum change — a short outline and at most "
    "2 sample assessment items — as a starting point for a human author, not finished "
    "content. Do NOT invent benchmarks, dates, or specific numbers; put anything a human "
    "must verify in caveats."
)

# NOTE (public mirror): proprietary rubric withheld — generic extraction brief.
_SYLLABUS_SYSTEM_PROMPT = (
    "You extract course syllabi for curriculum-intelligence. Given raw text of a university "
    "course page, extract ONLY what the page states. Never invent topics, weeks, or facts. "
    "If the page lacks a syllabus, say so via low confidence and few topics."
)

# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# preserves the structured contract — seven 0-1 dimensions and the
# adopt_now/monitor/reject recommendation — plus the evidence-grounded, honest
# rules; the production scoring heuristics are not shipped.
_JUDGE_SYSTEM_PROMPT = (
    "You are a curriculum investment judge. Given (1) a detected curriculum gap, (2) the "
    "topics the curriculum already covers, and (3) an evidence dossier accumulated across "
    "runs, score the net benefit of adopting the change on seven 0-1 dimensions and "
    "recommend adopt_now, monitor, or reject.\n\n"
    "Rules:\n"
    "- Judge ONLY from the dossier evidence and covered topics; do not rely on outside "
    "knowledge, and score a dimension you cannot evidence at 0.5 (unknown).\n"
    "- Be conservative: adopt_now is for clear, multi-source, current demand; reject noise "
    "and out-of-scope trivia with no durable skill value."
)

# NOTE (public mirror): proprietary rubric withheld — generic generator brief;
# the edit/new-mode contract and never-invent/caveats honesty rule are kept.
_GENERATOR_SYSTEM_PROMPT = (
    "You write curriculum content for a proposed change a human will review as a diff and "
    "approve or reject. Given the change mode ('edit' with the asset's current content, or "
    "'new' for a brand-new asset), a draft outline, the evidence dossier, and style samples "
    "from sibling assets, produce the COMPLETE new content body.\n\n"
    "Rules:\n"
    "- EDIT mode: preserve the asset's existing structure and voice — aim for a minimal diff.\n"
    "- NEW mode: match the style samples' structure and voice.\n"
    "- Never invent benchmarks, dates, version numbers, or statistics; put anything "
    "requiring verification into caveats. Assessments must include answers or rubrics."
)


# --- Authoring Platform slice 3: per-aspect generator system prompts ---
#
# These extend the co-pilots from advisory to GENERATIVE. Each produces an
# editable draft a human author reviews; nothing auto-applies, and the
# mandatory QA -> approval -> release gate still stands. The prompts carry the
# freshness-pipeline discipline: grounded in the given inputs, conservative,
# never invent facts/citations/benchmarks, surface uncertainty in caveats.

# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# keeps the structured contract (one Bloom-tagged, measurable outcome per
# objective; bloom_level matches the verb) and the advisory/grounding rules.
_OBJECTIVES_SYSTEM_PROMPT = (
    "You are a curriculum designer drafting learning objectives for a course an "
    "author is building. Given a topic and a learner profile, draft a set of "
    "measurable, Bloom-tagged learning objectives.\n\n"
    "Rules:\n"
    "- Ground every objective in the given topic and learner profile; do NOT invent "
    "scope the topic does not imply.\n"
    "- Each objective states ONE observable, assessable outcome starting with a "
    "Bloom verb; bloom_level MUST match that verb.\n"
    "- This is a STARTING DRAFT a human author reviews and edits — not a final, "
    "authoritative syllabus."
)

# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# keeps the structured contract (a Markdown body shaped to the asset kind and
# the objective's Bloom level) and the never-invent/caveats honesty rule.
_ITEM_CONTENT_SYSTEM_PROMPT = (
    "You are a curriculum author drafting the body of ONE course content item. "
    "Given a target learning objective, the asset kind, and the surrounding course "
    "context, write the complete item body in Markdown that teaches the objective "
    "at its Bloom level, shaped to the asset kind and consistent with the given "
    "course context.\n\n"
    "Rules:\n"
    "- Never invent benchmarks, dates, versions, statistics, or citations. Put "
    "anything a human must verify — and anything you were unsure of — into caveats "
    "instead of asserting it as fact.\n"
    "- This is an ADVISORY draft the author reviews and edits; it still passes the "
    "mandatory QA gate before it can ship."
)

# NOTE (public mirror): proprietary rubric withheld. The generic instruction
# keeps the structured contract (assessment measuring the objective + an aligned
# scoring rubric, with an answer key) and the never-invent/caveats honesty rule.
_ASSESSMENT_SYSTEM_PROMPT = (
    "You are a curriculum author drafting an assessment and its scoring rubric for "
    "ONE learning objective, given the surrounding course context. The assessment "
    "must measure whether the learner met the objective at its Bloom level and "
    "include an answer key or expected-response notes; the rubric's criteria and "
    "levels must align with what the assessment asks.\n\n"
    "Rules:\n"
    "- Never invent facts, statistics, or citations. Surface anything a human must "
    "verify, and anything you were unsure of, in caveats.\n"
    "- This is an ADVISORY draft the author reviews and edits; it still passes the "
    "mandatory QA gate before it can ship."
)


# T3a — multilingual generation. The default English tokens map to NO suffix so
# a default brief produces byte-identical prompts (current behaviour preserved);
# any other value appends an explicit target-language instruction to the prompt.
_DEFAULT_LANGUAGE_TOKENS = {"", "en", "english"}


def _generate_in(language: str) -> str:
    """Prompt suffix instructing the generator to emit content in ``language``.

    Empty for the English default (``en`` / ``english`` / blank), so the default
    brief's generator prompts are unchanged; otherwise a single trailing
    instruction ``"\\n\\nGenerate all content in {language}."``.
    """
    if language.strip().lower() in _DEFAULT_LANGUAGE_TOKENS:
        return ""
    return f"\n\nGenerate all content in {language.strip()}."


def _build_placement_prompt(finding: GapFinding, structure: CurriculumStructure) -> str:
    mods = "\n".join(f"  - module index {m.index}: {m.focus or '(no focus)'}" for m in structure.modules)
    projs = "\n".join(f"  - project index {p.index}: {p.title}" for p in structure.projects)
    assets = "\n".join(
        f"  - asset key '{a.key}' (kind={a.kind}, module={a.module_index})" for a in structure.assets
    )
    return (
        f"GAP\n  topic: {finding.topic}\n  coverage: {finding.coverage_status}\n"
        f"  rationale: {finding.rationale}\n  evidence: {finding.evidence}\n\n"
        f"CURRICULUM STRUCTURE\nModules:\n{mods or '  (none)'}\n"
        f"Projects:\n{projs or '  (none)'}\nAssets:\n{assets or '  (none)'}\n"
    )


def _build_draft_prompt(finding: GapFinding, placement: Placement) -> str:
    return (
        f"GAP\n  topic: {finding.topic}\n  rationale: {finding.rationale}\n\n"
        f"PLACEMENT\n  {placement.target_kind} {placement.target_ref or ''} — {placement.position_hint}\n\n"
        "Draft the starter frame (outline + <=2 sample assessments) for integrating this gap here."
    )


class AIClient(GapExtractor, GapEnricher, QAJudge, WebSearcher, ImpactAnalyzer, Categorizer, AndragogyAdvisor, PrereqInferer, SyllabusExtractor, GapJudge, ContentGenerator, CourseAuthorAI, DeckGenerator, Tutor):
    """Real Anthropic-backed gap extractor, QA judge, web searcher, impact analyst,
    item categorizer, andragogy advisor, prerequisite inferer, syllabus extractor,
    gap investment judge, content generator, and per-aspect course-author generator.

    Satisfies twelve seams: ``GapExtractor`` (C2), ``GapEnricher`` (placed
    draft proposal), ``QAJudge`` (C3), ``WebSearcher`` (V2 live corpus),
    ``ImpactAnalyzer`` (Milestone B CCR-impact guidance), ``Categorizer``
    (Phase 2 item AI), ``AndragogyAdvisor`` (Phase 2 course AI),
    ``PrereqInferer`` (Phase 2 Task 2 prerequisite inference),
    ``SyllabusExtractor`` (freshness pipeline university adapter),
    ``GapJudge`` (freshness pipeline Phase 2 net-benefit judge),
    ``ContentGenerator`` (freshness pipeline Phase 3 change_set content), and
    ``CourseAuthorAI`` (Authoring Platform slice 3 per-aspect generators). The
    underlying ``anthropic.AsyncAnthropic`` client is constructed lazily on
    the first call, never at import or construction time. We use the async
    client (not a thread offload) because the Anthropic call is multi-second
    and must not freeze the uvicorn event loop.
    """

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self._api_key = api_key
        # Default to the env-configured model (settings.AI_MODEL) so all
        # construction sites pick up AI_MODEL automatically; an explicit
        # model= still wins. Read at construction time.
        self.model = model or settings.AI_MODEL
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def _invoke(
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_format: type[BaseModel],
    ):
        """One structured-output API call + its per-call telemetry.

        Returns the raw response (the caller inspects ``stop_reason`` /
        ``parsed_output``). Telemetry is keyed on the ACTUAL ``model`` used so a
        refused call and its fallback are each priced honestly. opus-4-8 + Fable-5
        both support adaptive thinking + effort=high. Best-effort telemetry:
        aggregator / persist failures warn but never break the AI call.
        """
        start = time.monotonic()
        resp = await self.client.messages.parse(
            model=model,
            max_tokens=_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        # Per-call observability: one structured INFO line carrying latency +
        # token usage + stop reason. usage/stop_reason may be absent on some
        # responses, so read them defensively.
        resp_usage = getattr(resp, "usage", None)
        in_tok = getattr(resp_usage, "input_tokens", None)
        out_tok = getattr(resp_usage, "output_tokens", None)
        stop = getattr(resp, "stop_reason", None)
        logger.info(
            "ai_call model=%s output=%s in_tokens=%s out_tokens=%s "
            "latency_ms=%d stop=%s",
            model,
            output_format.__name__,
            in_tok,
            out_tok,
            latency_ms,
            stop,
        )
        # Feed the in-process usage aggregator (tokens/latency/cost). This is
        # hot-path and best-effort: a failure here must NEVER break the AI
        # call, so swallow + warn rather than propagate.
        try:
            usage.record(
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                stop_reason=stop,
            )
        except Exception:  # noqa: BLE001 — telemetry must not break the call
            logger.warning("ai usage aggregator record() failed", exc_info=True)
        # Durable per-call usage row (fire-and-forget, off the hot path). Like
        # the aggregator above, it is best-effort: a scheduling/import failure
        # here must NEVER break the AI call, so swallow + warn.
        try:
            usage_store.record_event(
                model=model,
                feature=output_format.__name__,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                stop_reason=stop,
            )
        except Exception:  # noqa: BLE001 — telemetry must not break the call
            logger.warning("ai usage_store record_event() failed", exc_info=True)
        return resp

    async def _parse(self, *, system: str, user: str, output_format: type[BaseModel]):
        """Shared structured-output call with a single refusal fallback.

        Fable-5's safety classifiers can DECLINE a request, returning
        ``stop_reason="refusal"`` as a successful HTTP 200 with no structured
        output (not an HTTP error). When the configured model refuses, we fall
        back to Opus 4.8 ONCE (Opus does not refuse this way). Both the refused
        call and the fallback are recorded in telemetry for honest cost
        visibility. Surfaces a clear error if no structured output is returned;
        lets all other API/parse errors propagate — never swallows.
        """
        resp = await self._invoke(
            model=self.model, system=system, user=user, output_format=output_format
        )
        if (
            getattr(resp, "stop_reason", None) == "refusal"
            and self.model != _FALLBACK_MODEL
        ):
            logger.warning(
                "model %s refused (stop_reason=refusal); falling back to %s",
                self.model,
                _FALLBACK_MODEL,
            )
            resp = await self._invoke(
                model=_FALLBACK_MODEL,
                system=system,
                user=user,
                output_format=output_format,
            )
        # parsed_output can be None (e.g. an adaptive-thinking response with no
        # text block, or a refusal) — surface a clear error instead of a bare
        # AttributeError.
        parsed = resp.parsed_output
        if parsed is None:
            if getattr(resp, "stop_reason", None) == "refusal":
                raise ValueError(
                    f"Model refused the request (stop_reason=refusal) for "
                    f"{output_format.__name__}"
                )
            raise ValueError(
                f"Anthropic returned no structured output for {output_format.__name__}"
            )
        return parsed

    async def answer_question(
        self, *, question: str, context_chunks: list[str], language: str = "en"
    ) -> TutorAnswer:
        """Grounded RAG tutor answer (Phase B, B3) — governed via ``_parse``.

        Sees ONLY the (already PII-redacted) ``question`` and the retrieved
        course ``context_chunks`` — never any learner identity (D5). The
        orchestrator (``app/core/tutor/answer.py``) guarantees this is called
        only when ``context_chunks`` is non-empty (the grounding gate), so the
        model is never asked to answer from no context. ``language`` (T3b) steers
        only the reply language and carries no identity — D5 is unaffected.
        """
        return await self._parse(
            system=TUTOR_SYSTEM_PROMPT,
            user=build_tutor_user_prompt(question, context_chunks, language),
            output_format=TutorAnswer,
        )

    async def generate_coaching(
        self, *, progress: str, context_chunks: list[str], language: str = "en"
    ) -> CoachMessage:
        """Proactive coaching turn (Phase B, B4) — governed via ``_parse``.

        Sees ONLY the anonymized ``progress`` signal (counts + next-item label,
        never identity) and the retrieved course ``context_chunks`` (D5). The
        orchestrator (``app/core/tutor/coach.py``) supplies both; empty
        ``context_chunks`` degrades to process-level coaching (no fabrication).
        ``language`` (T3b) steers only the reply language — no identity (D5 holds).
        """
        return await self._parse(
            system=COACH_SYSTEM_PROMPT,
            user=build_coach_user_prompt(progress, context_chunks, language),
            output_format=CoachMessage,
        )

    async def evaluate_submission(
        self, *, rubric: str, assessment_prompt: str, response: str,
        language: str = "en",
    ) -> AssessmentEvaluation:
        """Grade a submission against its rubric (Phase B, B5) — via ``_parse``.

        Sees ONLY course material (``rubric`` + ``assessment_prompt``) and the
        learner's PII-redacted ``response`` — never any learner identity (D5).
        The orchestrator (``app/core/tutor/assess.py``) writes the returned
        score/feedback back onto the submission. ``language`` (T3b) localizes only
        the feedback text — no identity (D5 holds); the score stays numeric.
        """
        return await self._parse(
            system=ASSESS_SYSTEM_PROMPT,
            user=build_assess_user_prompt(rubric, assessment_prompt, response, language),
            output_format=AssessmentEvaluation,
        )

    async def extract_gaps(
        self,
        covered_topics: list[str],
        corpus_docs: list[CorpusDoc],
        covered_content: list[dict] | None = None,
    ) -> list[GapFinding]:
        user = _build_user_prompt(covered_topics, corpus_docs, covered_content)
        system = (
            _SYSTEM_PROMPT + _CONTENT_AWARE_SYSTEM_SUFFIX
            if covered_content is not None
            else _SYSTEM_PROMPT
        )
        report = await self._parse(
            system=system, user=user, output_format=GapReport
        )
        return report.findings

    async def place_gap(self, finding: GapFinding, structure: CurriculumStructure) -> Placement:
        return await self._parse(
            system=_PLACEMENT_SYSTEM_PROMPT,
            user=_build_placement_prompt(finding, structure),
            output_format=Placement,
        )

    async def draft_frame(self, finding: GapFinding, placement: Placement) -> DraftFrame:
        return await self._parse(
            system=_DRAFT_SYSTEM_PROMPT,
            user=_build_draft_prompt(finding, placement),
            output_format=DraftFrame,
        )

    async def judge(self, ccr_summary: str, proposed_changes: str) -> QAJudgement:
        """Score a CCR's six QA dimensions (1–5 + evidence). Advisory only.

        The returned ``QAJudgement`` is validated to cover exactly the six
        canonical dimensions; a malformed judgement raises rather than yielding
        a partial review.
        """
        user = (
            f"CCR SUMMARY:\n{ccr_summary}\n\n"
            f"PROPOSED CHANGES:\n{proposed_changes}\n\n"
            f"Score each of these six dimensions: {', '.join(QA_DIMENSIONS)}."
        )
        return await self._parse(
            system=_QA_SYSTEM_PROMPT, user=user, output_format=QAJudgement
        )

    async def analyze_impact(
        self,
        *,
        change_set: ReleaseChangeSet,
        title: str | None = None,
        rationale: str | None = None,
        context: str | None = None,
    ) -> ImpactReport:
        """Estimate a change-set's impact (objectives / duration / cognitive load).

        Advisory only — a human author decides. Builds the user message from the
        change-set (+ optional title/rationale/curriculum context) and returns a
        validated ``ImpactReport``; errors propagate.
        """
        user = build_user_prompt(
            change_set=change_set,
            title=title,
            rationale=rationale,
            context=context,
        )
        return await self._parse(
            system=IMPACT_SYSTEM_PROMPT, user=user, output_format=ImpactReport
        )

    async def web_search_corpus(
        self, query: str, max_results: int
    ) -> list[CorpusDoc]:
        """Live field signal: web-search current industry demand for ``query``.

        Uses Anthropic's web_search SERVER tool (Claude runs the searches
        server-side and returns cited results). We use ``messages.create`` here,
        NOT ``messages.parse`` — server tools and forced structured output don't
        compose cleanly. This call matches the documented web-search server-tool
        shape exactly (``model``, ``max_tokens``, ``system``, ``messages``,
        ``tools`` only — no ``thinking``/``output_config``, which the documented
        web-search examples do not pass). Tool shape + response parsing per the
        Claude API docs:
        web_search server-tool block is ``{"type": "web_search_20260209",
        "name": "web_search"}``; results arrive as ``web_search_tool_result``
        blocks (items: ``web_search_result`` with ``url``/``title``/``page_age``),
        and human-readable snippets arrive as ``web_search_result_location``
        citations (``cited_text``) on the text blocks.
        (docs: agents-and-tools/tool-use/web-search-tool — "Response" section.)

        Returns one ``CorpusDoc`` per distinct source (``kind="live_search"``,
        ``title`` = source title or URL, ``body`` = snippet + URL), truncated to
        ``max_results``. Errors are surfaced, never swallowed.
        """
        user = (
            f"Curriculum domain: {query}\n\n"
            "Search the web for the current (2026+) industry and job-market "
            "skill expectations for this domain. Return a thorough set of the "
            "distinct in-demand skills/tools/topics, each grounded in a cited "
            "source."
        )
        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=_WEB_SEARCH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
        )

        # Web search runs a server-side sampling loop; if it hits the iteration
        # cap the turn pauses (stop_reason="pause_turn") and only partial results
        # are delivered. Surface it instead of silently returning a thin corpus.
        if getattr(resp, "stop_reason", None) == "pause_turn":
            raise RuntimeError(
                "web_search turn was paused (stop_reason=pause_turn); partial "
                "results discarded — retry or raise max_tokens."
            )

        # Collect the human-readable snippet per source URL from the citations
        # that Claude attached to its text blocks (cited_text is the readable
        # excerpt; encrypted_content on the raw result is not human-readable).
        snippets: dict[str, str] = {}
        for block in resp.content:
            for citation in getattr(block, "citations", None) or []:
                if getattr(citation, "type", None) == "web_search_result_location":
                    url = getattr(citation, "url", None)
                    cited = getattr(citation, "cited_text", None)
                    if url and cited and url not in snippets:
                        snippets[url] = cited

        # Walk the web_search_tool_result blocks to build the source list (these
        # carry the canonical title+url for every result Claude saw).
        docs: list[CorpusDoc] = []
        seen: set[str] = set()
        for block in resp.content:
            if getattr(block, "type", None) != "web_search_tool_result":
                continue
            items = block.content
            # On a tool error the content is a single error object, not a list
            # (e.g. too_many_requests / max_uses_exceeded). Surface it — don't
            # swallow a failed search behind an empty corpus.
            if not isinstance(items, list):
                logger.warning(
                    "web_search returned a tool-error block (no results): %r", items
                )
                continue
            for item in items:
                if getattr(item, "type", None) != "web_search_result":
                    continue
                url = getattr(item, "url", "") or ""
                title = getattr(item, "title", "") or url
                if not url or url in seen:
                    continue
                seen.add(url)
                snippet = snippets.get(url, "")
                body = f"{snippet}\n\nSource: {url}" if snippet else f"Source: {url}"
                docs.append(
                    CorpusDoc(title=title[:512], kind="live_search", body=body)
                )
                if len(docs) >= max_results:
                    return docs
        return docs

    async def categorize(
        self,
        *,
        title: str,
        content: str | None,
        objectives: list[str],
    ) -> CategorizeResult:
        """Classify a draft item into an asset kind + estimate effort. Advisory only.

        Builds the user message from the item title, optional content excerpt,
        and the course objectives, then calls ``_parse`` for a validated
        ``CategorizeResult``. Errors propagate — never swallowed.
        """
        user = build_categorize_prompt(
            title=title,
            content=content,
            objectives=objectives,
        )
        return await self._parse(
            system=CATEGORIZE_SYSTEM_PROMPT,
            user=user,
            output_format=CategorizeResult,
        )

    async def advise(
        self,
        *,
        course_context: str,
        learner_profile: dict,
        focus: str | None = None,
    ) -> AdviceReport:
        """Produce Knowles-grounded andragogy advisory notes for a draft course.

        Builds the user message from the course context summary, learner
        profile, and optional focus area, then calls ``_parse`` for a
        validated ``AdviceReport``. Errors propagate — never swallowed.
        """
        user = build_advise_prompt(
            course_context=course_context,
            learner_profile=learner_profile,
            focus=focus,
        )
        return await self._parse(
            system=ANDRAGOGY_SYSTEM_PROMPT,
            user=user,
            output_format=AdviceReport,
        )

    async def infer(self, *, items: list[dict]) -> PrereqReport:
        """Propose prerequisite edges + flag missing dependencies for a draft course.

        Advisory only — a human author reviews every suggestion and decides.
        Builds the user message from the item list and returns a validated
        ``PrereqReport``; errors propagate — never swallowed.
        """
        user = build_prereq_prompt(items=items)
        return await self._parse(
            system=PREREQ_SYSTEM_PROMPT,
            user=user,
            output_format=PrereqReport,
        )

    async def extract_syllabus(self, page_text: str, context: str) -> SyllabusExtract:
        """Extract structured syllabus data from raw university course page text.

        ``context`` is a human-readable label (e.g. "UC Berkeley — CS294 Agentic AI")
        used to orient the model. ``page_text`` is the raw text of the course page,
        already stripped of HTML by the caller. Errors propagate — never swallowed.
        """
        user = (
            f"Course context: {context}\n\n"
            f"Page text:\n{page_text}"
        )
        return await self._parse(
            system=_SYLLABUS_SYSTEM_PROMPT,
            user=user,
            output_format=SyllabusExtract,
        )

    async def judge_gap(
        self,
        finding: GapFinding,
        covered_topics: list[str],
        dossier: list[dict],
    ) -> NetBenefitAssessment:
        """Judge a detected gap's net benefit against covered topics and the accumulated dossier.

        Advisory — the caller gates on ``recommendation == 'adopt_now'`` AND a
        configured confidence threshold. Errors propagate — never swallowed.
        """
        covered = "\n".join(f"- {t}" for t in covered_topics) or "(none)"
        user = (
            f"GAP FINDING\n"
            f"  topic: {finding.topic}\n"
            f"  coverage_status: {finding.coverage_status}\n"
            f"  rationale: {finding.rationale}\n"
            f"  evidence: {finding.evidence}\n\n"
            f"COVERED TOPICS:\n{covered}\n\n"
            f"EVIDENCE DOSSIER (all accumulated sightings):\n"
            f"{json.dumps(dossier, indent=2)}"
        )
        return await self._parse(
            system=_JUDGE_SYSTEM_PROMPT,
            user=user,
            output_format=NetBenefitAssessment,
        )

    async def generate_asset_content(
        self,
        *,
        mode: str,
        current_content: str | None,
        draft_frame: dict,
        dossier: list[dict],
        style_samples: list[str],
        asset_kind: str,
        topic: str,
    ) -> GeneratedAssetContent:
        """Generate the complete content body for one asset in a proposed change_set.

        ``mode`` is ``'edit'`` (existing asset — ``current_content`` provided) or
        ``'new'`` (brand-new asset — ``current_content`` is None). The caller
        supplies the draft outline (``draft_frame``), the accumulated evidence
        dossier, style samples from sibling assets, the asset kind, and the gap
        topic. Advisory — the result enters the existing human QA + approval +
        merge gates as a normal change_set. Errors propagate — never swallowed.
        """
        current_section = (
            f"CURRENT CONTENT:\n{current_content}"
            if current_content is not None
            else "CURRENT CONTENT:\n(new asset — no current content)"
        )
        samples_section = "\n\n".join(
            f"STYLE SAMPLE {i + 1}:\n{s}" for i, s in enumerate(style_samples)
        ) or "(no style samples)"
        user = (
            f"MODE: {mode}\n\n"
            f"ASSET KIND: {asset_kind}\n"
            f"TOPIC: {topic}\n\n"
            f"{current_section}\n\n"
            f"DRAFT FRAME:\n{json.dumps(draft_frame, indent=2)}\n\n"
            f"EVIDENCE DOSSIER:\n{json.dumps(dossier, indent=2)}\n\n"
            f"STYLE SAMPLES:\n{samples_section}"
        )
        return await self._parse(
            system=_GENERATOR_SYSTEM_PROMPT,
            user=user,
            output_format=GeneratedAssetContent,
        )

    # -- Authoring Platform slice 3: per-aspect generators (CourseAuthorAI) --

    async def generate_objectives(
        self, *, topic: str, learner_profile: dict, count: int = 5,
        language: str = "en",
    ) -> GeneratedObjectives:
        """Draft a set of Bloom-tagged learning objectives for a topic.

        ADVISORY — the author curates/edits the returned list client-side; nothing
        is written into the draft here. Grounds the model in the topic and learner
        profile; errors propagate — never swallowed. ``language`` (T3a) appends a
        target-language instruction when non-default (``en`` leaves it unchanged).
        """
        user = (
            f"TOPIC:\n{topic}\n\n"
            f"LEARNER PROFILE:\n{json.dumps(learner_profile, indent=2)}\n\n"
            f"Draft {count} learning objective(s) for this topic and audience."
            f"{_generate_in(language)}"
        )
        return await self._parse(
            system=_OBJECTIVES_SYSTEM_PROMPT,
            user=user,
            output_format=GeneratedObjectives,
        )

    async def generate_item_content(
        self, *, objective: str, kind: str, course_context: str,
        language: str = "en",
    ) -> GeneratedItemContent:
        """Draft the body of ONE course content item for a target objective + kind.

        ADVISORY — the author reviews/edits/accepts it; nothing is auto-written into
        the draft. Grounds the model in the objective, the asset kind, and the
        surrounding course context; errors propagate — never swallowed. ``language``
        (T3a) appends a target-language instruction when non-default.
        """
        user = (
            f"ASSET KIND: {kind}\n\n"
            f"TARGET OBJECTIVE:\n{objective}\n\n"
            f"COURSE CONTEXT:\n{course_context}\n\n"
            f"Write the complete {kind} body (Markdown) that teaches this objective."
            f"{_generate_in(language)}"
        )
        return await self._parse(
            system=_ITEM_CONTENT_SYSTEM_PROMPT,
            user=user,
            output_format=GeneratedItemContent,
        )

    async def generate_assessment(
        self, *, objective: str, course_context: str, language: str = "en",
    ) -> GeneratedAssessment:
        """Draft an assessment + rubric measuring ONE learning objective.

        ADVISORY — the author reviews/edits/accepts it; nothing is auto-written into
        the draft, and it still passes the mandatory QA gate before release. Grounds
        the model in the objective + course context; errors propagate — never swallowed.
        ``language`` (T3a) appends a target-language instruction when non-default.
        """
        user = (
            f"TARGET OBJECTIVE:\n{objective}\n\n"
            f"COURSE CONTEXT:\n{course_context}\n\n"
            "Write an assessment measuring this objective plus a scoring rubric."
            f"{_generate_in(language)}"
        )
        return await self._parse(
            system=_ASSESSMENT_SYSTEM_PROMPT,
            user=user,
            output_format=GeneratedAssessment,
        )

    # -- Slide System Port slice 2 (S2): deck generator (DeckGenerator) --

    async def generate_deck(
        self,
        *,
        module_title: str,
        module_number: str,
        module_id: str,
        objectives: list[dict],
        items: list[dict],
        bloom_ceiling: str | None = None,
        language: str = "en",
    ) -> GeneratedDeck:
        """Author a complete Marp ``deck.md`` for a module — governed via ``_parse``.

        Grounded strictly in the module's ``objectives`` (the deck's spine) and its
        content ``items`` (the substance). ADVISORY (D-2): the returned deck is reviewed by a human before
        release, never auto-published; real visual quality is only proven once
        rendered via the S1 pipeline. Errors propagate — never swallowed.
        ``language`` steers only the authored prose (front-matter/code identifiers
        stay unchanged) and is empty-suffix for the English default.
        """
        user = build_deck_user_prompt(
            module_title=module_title,
            module_number=module_number,
            module_id=module_id,
            objectives=objectives,
            items=items,
            bloom_ceiling=bloom_ceiling,
            language=language,
        )
        return await self._parse(
            system=DECK_SYSTEM_PROMPT,
            user=user,
            output_format=GeneratedDeck,
        )
