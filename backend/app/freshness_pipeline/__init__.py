"""freshness_pipeline package.

Exports the shared PipelineSignal dataclass consumed by all adapters
(industry, university) and the runner.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineSignal:
    """A single intelligence signal produced by a pipeline adapter.

    Fields
    ------
    id          Stable identifier — guid/permalink from the source entry, or a
                deterministic hash for signals without a natural unique key.
    source_kind Adapter category: "industry_news" | "university_syllabus".
    source      Human-readable source name (e.g. "openai", "simonwillison").
    title       Entry headline or topic phrase.
    url         Canonical URL for the entry.
    detail      Body / summary text, truncated to ~2 000 chars.
    captured_at ISO-ish timestamp string from the source (feed's
                updated/published field, or fetch timestamp).
    """

    id: str
    source_kind: str
    source: str
    title: str
    url: str
    detail: str
    captured_at: str
