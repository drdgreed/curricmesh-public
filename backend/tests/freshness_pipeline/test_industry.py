"""TDD tests for the industry news adapter.

Red state: these tests fail until app/freshness_pipeline/__init__.py and
app/freshness_pipeline/industry.py are created.

Two coverage targets:
  1. parse_feed — static RSS/Atom string → list[PipelineSignal] with stable ids
     and all required fields (source_kind, source, title, url, detail,
     captured_at).
  2. fetch_all — per-feed failure isolation: one bad feed must never kill the
     run; signals from the working feeds are still returned.

No live network touches anywhere in this module.
"""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.freshness_pipeline import PipelineSignal
from app.freshness_pipeline.industry import FEEDS, fetch_all, parse_feed

# ---------------------------------------------------------------------------
# Small static RSS fixture
# ---------------------------------------------------------------------------

_RSS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <link>https://example.com</link>
        <description>A test feed</description>
        <item>
          <title>GPT-5 Released</title>
          <link>https://openai.com/blog/gpt-5</link>
          <guid isPermaLink="false">tag:openai.com,2025:gpt5</guid>
          <description>OpenAI launches GPT-5 with improved reasoning.</description>
          <pubDate>Wed, 01 Jan 2025 12:00:00 +0000</pubDate>
        </item>
        <item>
          <title>New Safety Research</title>
          <link>https://openai.com/blog/safety</link>
          <description>Summary of safety work without a guid.</description>
          <pubDate>Thu, 02 Jan 2025 09:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>
""")

_ATOM = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Simon Willison's Weblog</title>
      <id>https://simonwillison.net/</id>
      <entry>
        <title>Notes on LLM tool use</title>
        <id>https://simonwillison.net/2025/Jan/3/tool-use/</id>
        <link href="https://simonwillison.net/2025/Jan/3/tool-use/"/>
        <summary>Thoughts on how LLMs call tools.</summary>
        <updated>2025-01-03T08:00:00Z</updated>
      </entry>
    </feed>
""")


# ---------------------------------------------------------------------------
# parse_feed — field correctness
# ---------------------------------------------------------------------------


def test_parse_feed_returns_pipeline_signals():
    """parse_feed on a small RSS string returns a list of PipelineSignal."""
    signals = parse_feed(_RSS, source="openai")
    assert len(signals) == 2
    for sig in signals:
        assert isinstance(sig, PipelineSignal)


def test_parse_feed_source_kind_is_industry_news():
    """source_kind must be 'industry_news' for all industry signals."""
    signals = parse_feed(_RSS, source="openai")
    assert all(s.source_kind == "industry_news" for s in signals)


def test_parse_feed_source_field():
    """source field reflects the caller-supplied source name."""
    signals = parse_feed(_RSS, source="openai")
    assert all(s.source == "openai" for s in signals)


def test_parse_feed_title_and_url():
    """title and url are extracted from the feed entry."""
    signals = parse_feed(_RSS, source="openai")
    assert signals[0].title == "GPT-5 Released"
    assert signals[0].url == "https://openai.com/blog/gpt-5"


def test_parse_feed_detail_is_summary_truncated():
    """detail is the entry summary truncated to ~2000 chars."""
    signals = parse_feed(_RSS, source="openai")
    assert "OpenAI launches GPT-5" in signals[0].detail
    assert len(signals[0].detail) <= 2000


def test_parse_feed_stable_id_uses_guid():
    """When a guid is present it is used as the signal id (stable)."""
    signals = parse_feed(_RSS, source="openai")
    # First item has <guid>tag:openai.com,2025:gpt5</guid>
    assert signals[0].id == "tag:openai.com,2025:gpt5"


def test_parse_feed_stable_id_falls_back_to_link():
    """When no guid, id falls back to the entry link."""
    signals = parse_feed(_RSS, source="openai")
    # Second item has no guid — should fall back to link
    assert signals[1].id == "https://openai.com/blog/safety"


def test_parse_feed_captured_at():
    """captured_at is a non-empty string from the feed's published/updated field."""
    signals = parse_feed(_RSS, source="openai")
    # feedparser converts pubDate → 'published'; captured_at must be non-empty
    assert isinstance(signals[0].captured_at, str)
    assert signals[0].captured_at != ""


def test_parse_feed_atom():
    """parse_feed also works on Atom feeds (simonwillison uses Atom)."""
    signals = parse_feed(_ATOM, source="simonwillison")
    assert len(signals) == 1
    assert signals[0].source_kind == "industry_news"
    assert signals[0].title == "Notes on LLM tool use"
    assert signals[0].url == "https://simonwillison.net/2025/Jan/3/tool-use/"


# ---------------------------------------------------------------------------
# parse_feed — id stability: same raw → same id (deterministic, no randomness)
# ---------------------------------------------------------------------------


def test_parse_feed_id_is_deterministic():
    """Parsing the same RSS string twice yields identical ids."""
    s1 = parse_feed(_RSS, source="openai")
    s2 = parse_feed(_RSS, source="openai")
    assert [s.id for s in s1] == [s.id for s in s2]


# ---------------------------------------------------------------------------
# fetch_all — failure isolation
# ---------------------------------------------------------------------------


_GOOD_RSS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Good Feed</title>
        <link>https://good.example.com</link>
        <item>
          <title>Good Article</title>
          <link>https://good.example.com/art1</link>
          <guid>good:art1</guid>
          <description>A fine article from the working feed.</description>
          <pubDate>Mon, 01 Jan 2025 00:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>
""")


async def test_fetch_all_returns_signals_from_working_feeds():
    """fetch_all isolates a failing feed; the working feed's signals are returned."""
    feeds = {
        "good_feed": "https://good.example.com/rss.xml",
        "bad_feed": "https://broken.example.com/rss.xml",
    }

    good_response = MagicMock()
    good_response.status_code = 200
    good_response.raise_for_status = MagicMock()
    good_response.text = _GOOD_RSS

    async def _mock_get(url, **kwargs):
        if "good" in url:
            return good_response
        raise ConnectionError("simulated network failure")

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.freshness_pipeline.industry.httpx.AsyncClient", return_value=mock_client):
        signals = await fetch_all(feeds=feeds)

    # At least one signal from the working feed
    assert any(s.source == "good_feed" for s in signals)
    # No signals from the broken feed (not an error)
    assert not any(s.source == "bad_feed" for s in signals)


async def test_fetch_all_total_failure_returns_empty_not_raises():
    """If ALL feeds fail, fetch_all returns [] — never raises."""
    feeds = {"bad1": "https://a.example.com", "bad2": "https://b.example.com"}

    async def _mock_get(url, **kwargs):
        raise RuntimeError("everything is broken")

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.freshness_pipeline.industry.httpx.AsyncClient", return_value=mock_client):
        signals = await fetch_all(feeds=feeds)

    assert signals == []


async def test_fetch_all_logs_failure_not_raises(caplog):
    """A failing feed logs a WARNING (not print) and does not re-raise."""
    import logging

    feeds = {"failing": "https://fail.example.com/rss.xml"}

    async def _mock_get(url, **kwargs):
        raise ValueError("feed parse failure")

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.freshness_pipeline.industry.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level(logging.WARNING, logger="app.freshness_pipeline.industry"):
            signals = await fetch_all(feeds=feeds)

    assert signals == []
    # The failure should have been logged, not printed
    assert any("failing" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# FEEDS dict sanity
# ---------------------------------------------------------------------------


def test_feeds_contains_required_sources():
    """FEEDS must include the four canonical sources from the reference impl."""
    required = {"openai", "anthropic", "deepmind", "simonwillison"}
    assert required.issubset(set(FEEDS.keys()))


def test_feeds_values_are_https_urls():
    """All feed URLs use HTTPS."""
    for name, url in FEEDS.items():
        assert url.startswith("https://"), f"Feed '{name}' URL is not HTTPS: {url}"


async def test_fetch_all_isolates_parse_failure():
    """A PARSE-layer exception (not just network) is isolated per-feed too —
    the except scope must cover parse_feed, not only the HTTP fetch."""
    feeds = {
        "good_feed": "https://good.example.com/rss.xml",
        "parse_bomb": "https://weird.example.com/rss.xml",
    }
    ok = MagicMock()
    ok.status_code = 200
    ok.raise_for_status = MagicMock()
    ok.text = _GOOD_RSS

    async def _mock_get(url, **kwargs):
        return ok  # both feeds fetch fine

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    real_parse = parse_feed

    def _flaky_parse(raw, *, source):
        if source == "parse_bomb":
            raise ValueError("malformed xml")
        return real_parse(raw, source=source)

    with patch("app.freshness_pipeline.industry.httpx.AsyncClient", return_value=mock_client), \
         patch("app.freshness_pipeline.industry.parse_feed", side_effect=_flaky_parse):
        signals = await fetch_all(feeds=feeds)

    assert any(s.source == "good_feed" for s in signals)
    assert not any(s.source == "parse_bomb" for s in signals)


def test_feeds_anthropic_url_anchored():
    """The Anthropic feed is the non-obvious one (Google News RSS proxy) — anchor it."""
    assert "news.google.com/rss/search" in FEEDS["anthropic"]
    assert "Anthropic" in FEEDS["anthropic"]
