"""Industry news adapter for the freshness pipeline.

Ported from career-foundry's ``app/freshness/sources.py`` with the following
adaptations:
  - Uses ``PipelineSignal`` (id, source_kind, source, title, url, detail,
    captured_at) instead of the career-foundry ``Signal`` shape.
  - Uses module-level logging instead of print for feed failures.
  - ``source_kind`` is always ``"industry_news"`` for signals produced here.
  - ``detail`` maps to the entry summary (truncated ~2 000 chars).
  - ``captured_at`` maps to the entry's updated or published string.
"""

from __future__ import annotations

import logging

import feedparser
import httpx

from app.freshness_pipeline import PipelineSignal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feed registry
# ---------------------------------------------------------------------------

# Tier-1 lean-v1 seed list (extend from CURRICULUM_MAINTENANCE_PLAN later).
FEEDS: dict[str, str] = {
    # OpenAI publishes a full RSS feed (large history — first-run seeding +
    # the per-run cap bound it).
    "openai": "https://openai.com/news/rss.xml",
    # Anthropic has NO official RSS (verified 404), so track it via a Google
    # News RSS query.  Lower fidelity (third-party aggregation) but captures
    # announcements.
    "anthropic": "https://news.google.com/rss/search?q=Anthropic+Claude+when:21d&hl=en-US&gl=US&ceid=US:en",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "simonwillison": "https://simonwillison.net/atom/everything/",
}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_feed(raw: str, *, source: str) -> list[PipelineSignal]:
    """Parse a raw RSS/Atom string and return PipelineSignals.

    Parameters
    ----------
    raw:
        Raw XML/Atom text of the feed (not a URL — feedparser handles
        string input without any network I/O).
    source:
        Logical source name, e.g. ``"openai"``.

    Returns
    -------
    list[PipelineSignal]
        One signal per feed entry.  Empty list if the feed has no entries
        or feedparser cannot parse it.
    """
    parsed = feedparser.parse(raw)
    out: list[PipelineSignal] = []
    for entry in parsed.entries:
        # Prefer the feed's own entry id/guid; fall back to link, then title.
        sid = (
            getattr(entry, "id", None)
            or getattr(entry, "link", "")
            or getattr(entry, "title", "")
        )
        out.append(
            PipelineSignal(
                id=sid,
                source_kind="industry_news",
                source=source,
                title=getattr(entry, "title", "").strip(),
                url=getattr(entry, "link", ""),
                detail=getattr(entry, "summary", "").strip()[:2000],
                captured_at=getattr(
                    entry, "updated", getattr(entry, "published", "")
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


async def fetch_all(
    feeds: dict[str, str] | None = None,
    *,
    timeout: float = 20.0,
) -> list[PipelineSignal]:
    """Fetch all feeds and return their signals.

    One bad feed must never kill the run — each feed is wrapped in an
    individual try/except and failures are logged at WARNING level.

    Parameters
    ----------
    feeds:
        Optional override for the feed registry (defaults to ``FEEDS``).
        Primarily used in tests to inject a controlled set of URLs.
    timeout:
        Per-request timeout in seconds.

    Returns
    -------
    list[PipelineSignal]
        Aggregated signals from all feeds that succeeded.  Empty list if
        every feed fails.
    """
    feeds = feeds or FEEDS
    signals: list[PipelineSignal] = []
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "curricmesh-freshness/0.1"},
        follow_redirects=True,
    ) as client:
        for source, url in feeds.items():
            try:
                response = await client.get(url)
                response.raise_for_status()
                signals.extend(parse_feed(response.text, source=source))
            except Exception as exc:  # one bad feed must not kill the run
                logger.warning("feed '%s' failed: %s", source, exc)
    return signals
