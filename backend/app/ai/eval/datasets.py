"""Dataset loaders for the C4 AI evaluation harness.

DB-free: everything is read from JSON on disk via paths resolved relative to
this file (robust regardless of the process cwd). Two sources:

  - the C1 ground-truth (``PLANTED_GAPS.json`` + the synthetic SOTA corpus), and
  - C4-local synthetic fixtures (the bootcamp's covered topics and a small
    human-labeled QA set) shipped under ``fixtures/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.ai.schemas import CorpusDoc
from app.core.workflow.rules import QA_DIMENSIONS

# .../backend/app/ai/eval/datasets.py -> parents[3] == .../backend
_BACKEND = Path(__file__).resolve().parents[3]
_CORPUS_DIR = _BACKEND / "seed" / "sota_corpus"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_CURRICULA = _FIXTURES / "curricula"


def _load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_planted_gaps() -> list[dict]:
    """The 3 C1 ground-truth planted gaps (with canonical_tags + signal_strength)."""
    return _load_json(_CORPUS_DIR / "PLANTED_GAPS.json")["planted_gaps"]


def load_corpus() -> list[CorpusDoc]:
    """All 40 synthetic SOTA corpus docs (job postings + vendor docs)."""
    docs: list[CorpusDoc] = []
    for name in ("job_postings.json", "vendor_docs.json"):
        for row in _load_json(_CORPUS_DIR / name):
            docs.append(
                CorpusDoc(title=row["title"], kind=row["kind"], body=row["body"])
            )
    return docs


def bootcamp_covered_topics() -> list[str]:
    """Synthetic list of topics the bootcamp DOES cover.

    Deliberately excludes the three planted gaps so they are genuinely
    uncovered when measuring recall.
    """
    return _load_json(_FIXTURES / "bootcamp_covered_topics.json")["covered_topics"]


# ---------------------------------------------------------------------------
# Multi-curriculum eval (RE3): a LIST of curricula so gap detection is measured
# on more than one program and demonstrably generalizes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalCurriculum:
    """One curriculum under eval: its covered surface + corpus + ground truth.

    ``frozen=True`` prevents field reassignment; the loaders re-read JSON on each
    call so each ``load_eval_curricula()`` returns fresh list objects (don't rely
    on the contained lists themselves being deeply immutable). ``slug`` is the
    stable routing key (selects the matching replay fixture); ``name`` is the
    human-facing title.
    """

    name: str
    slug: str
    covered_topics: list[str]
    corpus: list[CorpusDoc]
    planted_gaps: list[dict]


def _load_curriculum_dir(slug: str, name: str) -> EvalCurriculum:
    """Build an ``EvalCurriculum`` from ``fixtures/curricula/<slug>/`` JSON."""
    base = _CURRICULA / slug
    covered = _load_json(base / "covered_topics.json")["covered_topics"]
    corpus = [
        CorpusDoc(title=row["title"], kind=row["kind"], body=row["body"])
        for row in _load_json(base / "corpus.json")
    ]
    planted = _load_json(base / "planted_gaps.json")["planted_gaps"]
    return EvalCurriculum(
        name=name,
        slug=slug,
        covered_topics=covered,
        corpus=corpus,
        planted_gaps=planted,
    )


def load_eval_curricula() -> list[EvalCurriculum]:
    """All curricula under eval.

    Curriculum 1 ("Agentic AI Architecture in Production") reuses the existing
    single-curriculum loaders verbatim — its ``covered_topics`` are kept CLEAN
    (no eval-routing marker injected) so a LIVE run never sees routing junk.

    Curriculum 2 ("Cloud Platform Engineering") is loaded from its own synthetic
    fixtures dir under ``fixtures/curricula/``.
    """
    return [
        EvalCurriculum(
            name="Agentic AI Architecture in Production",
            slug="agentic-ai-production",
            covered_topics=bootcamp_covered_topics(),
            corpus=load_corpus(),
            planted_gaps=load_planted_gaps(),
        ),
        _load_curriculum_dir(
            slug="cloud-platform-engineering",
            name="Cloud Platform Engineering",
        ),
    ]


def _validate_rater_scores(
    ccr_id: str, rater_id: str, scores: dict[str, int]
) -> None:
    """Raise ``ValueError`` unless ``scores`` covers EXACTLY the six dimensions.

    The message names BOTH the offending ``ccr_id`` and ``rater_id`` plus the
    missing / unexpected dims, so a malformed multi-rater fixture fails loudly
    and points straight at the bad cell rather than silently skewing agreement.
    """
    expected = set(QA_DIMENSIONS)
    got = set(scores.keys())
    if got != expected:
        raise ValueError(
            f"QA human label {ccr_id!r} rater {rater_id!r} must score exactly "
            f"the six dimensions. Missing: {sorted(expected - got)}. "
            f"Unexpected: {sorted(got - expected)}."
        )


def load_qa_human_labels() -> list[dict]:
    """Synthetic MULTI-RATER human-QA-Lead labels.

    Each row carries ``rater_scores`` mapping rater id -> that rater's 1-5 score
    on all six canonical ``QA_DIMENSIONS``. Validates that (a) every row has at
    least two raters (so a human inter-rater baseline is computable) and (b)
    every rater covers exactly the six dimensions, so a malformed fixture fails
    loudly rather than silently skewing agreement. Returns the rows unchanged
    (with ``rater_scores``).
    """
    rows = _load_json(_FIXTURES / "qa_human_labels.json")["labels"]
    for row in rows:
        ccr_id = row.get("ccr_id")
        rater_scores = row["rater_scores"]
        if len(rater_scores) < 2:
            raise ValueError(
                f"QA human label {ccr_id!r} must have at least 2 raters to "
                f"compute an inter-rater baseline; got {len(rater_scores)}."
            )
        for rater_id, scores in rater_scores.items():
            _validate_rater_scores(ccr_id, rater_id, scores)
    return rows
