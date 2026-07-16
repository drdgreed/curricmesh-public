"""Replay fakes for the C4 eval harness — deterministic, network-free.

These PLAIN classes structurally satisfy the ``GapExtractor`` / ``QAJudge``
Protocols (they have the right ``async def`` methods) without subclassing them.
They return canned model outputs recorded under ``fixtures/`` so CI runs the
real metrics machinery against a fixed snapshot — no Anthropic call, no API key.

The recorded outputs are a SNAPSHOT, clearly labeled as such in the report;
they are NOT live model output. Run live with ANTHROPIC_API_KEY set to exercise
the real ``AIClient`` through the identical seam.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ai.qa_judge import DimensionJudgement, QAJudgement
from app.ai.schemas import CorpusDoc, GapFinding

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str):
    with (_FIXTURES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


_DEFAULT_SLUG = "agentic-ai-production"


class ReplayExtractor:
    """Canned ``GapExtractor`` — returns recorded findings per curriculum.

    The fixture (``replay_findings.json``) is keyed by curriculum slug. The
    harness selects a curriculum by calling ``set_curriculum(slug)`` BEFORE
    ``extract_gaps`` — the routing key travels out-of-band, NOT inside the
    ``extract_gaps`` Protocol inputs, so no eval-routing marker is ever leaked
    into ``covered_topics`` and reaching a real model.

    The real ``AIClient`` has no ``set_curriculum``; the harness duck-types it
    (``if hasattr(extractor, "set_curriculum"): extractor.set_curriculum(slug)``),
    so the ``extract_gaps`` Protocol stays unchanged.

    Default slug is ``agentic-ai-production``: a bare ``ReplayExtractor()`` with
    no ``set_curriculum`` call returns curriculum 1's findings, preserving the
    documented 2/3 . 2/3 result and the existing e2e test.

    Intended per-curriculum metrics (computed by hand, asserted in tests):
      - agentic-ai-production:      precision 2/3, recall 2/3
      - cloud-platform-engineering: precision 3/4, recall 1.0
    """

    def __init__(self) -> None:
        data = _load_json("replay_findings.json")
        self._by_slug: dict[str, list[GapFinding]] = {
            slug: [GapFinding(**f) for f in findings]
            for slug, findings in data.items()
            if not slug.startswith("_")
        }
        self._slug = _DEFAULT_SLUG

    def set_curriculum(self, slug: str) -> None:
        """Route subsequent ``extract_gaps`` calls to ``slug``'s findings.

        Raises immediately on an unknown slug (e.g. a curriculum added to
        ``datasets.py`` without a matching ``replay_findings.json`` entry) so the
        failure points here with context, rather than surfacing as a bare
        ``KeyError`` later inside ``extract_gaps``.
        """
        if slug not in self._by_slug:
            raise KeyError(
                f"ReplayExtractor: no recorded findings for slug {slug!r}. "
                f"Available: {sorted(self._by_slug)}"
            )
        self._slug = slug

    async def extract_gaps(
        self, covered_topics: list[str], corpus_docs: list[CorpusDoc]
    ) -> list[GapFinding]:
        return list(self._by_slug[self._slug])


class ReplayJudge:
    """Canned ``QAJudge`` — returns recorded per-CCR judgements.

    Judgements are keyed by ccr_id in the fixture. ``judge`` receives only the
    summary/proposed-changes strings, so we route by matching the ccr_id token
    embedded in the summary; if none matches we fall back to the first recorded
    judgement (keeps the seam total and deterministic).
    """

    def __init__(self) -> None:
        data = _load_json("replay_qa.json")
        self._by_ccr: dict[str, QAJudgement] = {}
        for ccr_id, dims in data["judgements"].items():
            self._by_ccr[ccr_id] = QAJudgement(
                judgements=[
                    DimensionJudgement(
                        dimension=dim, score=v["score"], evidence=v["evidence"]
                    )
                    for dim, v in dims.items()
                ]
            )
        self._order = list(self._by_ccr)

    async def judge(self, ccr_summary: str, proposed_changes: str) -> QAJudgement:
        for ccr_id, judgement in self._by_ccr.items():
            if ccr_id in ccr_summary or ccr_id in proposed_changes:
                return judgement
        # Deterministic fallback (should not happen with the shipped fixtures).
        return self._by_ccr[self._order[0]]
