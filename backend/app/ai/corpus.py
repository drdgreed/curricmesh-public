"""Corpus provider seam for the SOTA-gap researcher (V2 live field signal).

The research router loads a corpus of ``SotaSource`` rows and hands it to
``analyze_gaps``. This module makes the corpus *source* swappable:

  - ``CuratedCorpusProvider`` (default) runs the existing curated query — the
    ``SotaSource`` rows already seeded in the DB. No network. Behavior identical
    to before this seam existed.
  - ``LiveCorpusProvider`` calls a ``WebSearcher`` (the real ``AIClient`` uses
    Anthropic's web_search server tool) to gather CURRENT industry demand,
    persists the results as ``kind="live_search"`` ``SotaSource`` rows for
    provenance, and returns them.

Both return ``list[SotaSource]`` so ``analyze_gaps`` (which converts rows ->
``CorpusDoc`` internally and computes covered topics) is UNCHANGED.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import WebSearcher
from app.models.curriculum import Curriculum
from app.models.sota import SotaSource

logger = logging.getLogger(__name__)

# The SOTA corpus is GLOBAL (shared industry signal, not per-curriculum). Cap it
# so the research call stays deterministic and within token limits.
_MAX_CORPUS = 200


@runtime_checkable
class CorpusProvider(Protocol):
    """Returns the SOTA corpus rows to research against, for a curriculum."""

    async def fetch(
        self, db: AsyncSession, curriculum: Curriculum
    ) -> list[SotaSource]: ...


class CuratedCorpusProvider:
    """Default provider: the existing curated ``SotaSource`` snapshot. No network."""

    async def fetch(
        self, db: AsyncSession, curriculum: Curriculum
    ) -> list[SotaSource]:
        result = await db.execute(
            select(SotaSource)
            .order_by(SotaSource.captured_at, SotaSource.id)
            .limit(_MAX_CORPUS)
        )
        corpus = list(result.scalars().all())
        if len(corpus) == _MAX_CORPUS:
            logger.warning(
                "SOTA corpus capped at %d sources for the research call", _MAX_CORPUS
            )
        return corpus


class LiveCorpusProvider:
    """Live provider: web-search current field signal, persist as provenance rows.

    Gated by the caller (the router only constructs this when
    ``LIVE_SOTA_ENABLED`` and ``ANTHROPIC_API_KEY`` are set).
    """

    def __init__(self, searcher: WebSearcher, max_results: int) -> None:
        self._searcher = searcher
        self._max_results = max_results

    async def fetch(
        self, db: AsyncSession, curriculum: Curriculum
    ) -> list[SotaSource]:
        # The curriculum name is the domain seed for the live search.
        docs = await self._searcher.web_search_corpus(
            curriculum.name, self._max_results
        )
        if not docs:
            logger.warning(
                "Live web search for %r returned zero results; the router's "
                "empty-corpus 400 will fire.",
                curriculum.name,
            )
            return []
        # The searcher already enforces the title[:512] limit on each CorpusDoc,
        # so no second truncation is needed here.
        rows = [
            SotaSource(title=d.title, kind="live_search", body=d.body)
            for d in docs
        ]
        # Persist for provenance (so findings cite real, auditable, current
        # evidence and the AI inbox can surface the source URLs) and so the rows
        # are flushed/identified before analyze_gaps runs over them.
        db.add_all(rows)
        await db.flush()
        return rows
