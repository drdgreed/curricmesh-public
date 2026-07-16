"""TDD tests for the university watchlist adapter.

Red state: these tests fail until app/freshness_pipeline/university.py is created.

Test coverage:
1. strip_html — pure function: tags, script blocks, entities, whitespace.
2. _topic_hash — pure function: lowercased, sorted, sha256.
3. check_watch_item:
   - first run: snapshot row created, returns []
   - changed topics: signals emitted, new snapshot stored
   - unchanged hash: no signals, no new snapshot row
   - fetch failure (exception): searcher used, snapshot marked search_only
   - fetch failure (HTTP 4xx): searcher used, snapshot marked search_only
   - dry_run on changed topics: signals returned, zero DB rows written
   - dry_run on first run: returns [], zero DB rows written

No live network or AI API calls anywhere.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import CorpusDoc, SyllabusExtract
from app.freshness_pipeline import PipelineSignal
from app.freshness_pipeline.university import _topic_hash, check_watch_item, strip_html
from app.models.freshness_pipeline import SourceWatchItem, SyllabusSnapshot
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


def _make_item(**kwargs) -> SourceWatchItem:
    defaults = dict(
        label="CS294 Agentic AI",
        institution="UC Berkeley",
        url="https://rdi.berkeley.edu/agentic-ai/f25",
        search_hint="Berkeley CS294 agentic AI syllabus",
    )
    defaults.update(kwargs)
    return SourceWatchItem(**defaults)


def _make_extract(
    topics: list[str],
    confidence: str = "high",
    quotes: list[str] | None = None,
) -> SyllabusExtract:
    return SyllabusExtract(
        course_title="CS294 Agentic AI",
        term="Fall 2025",
        topics=topics,
        notable=[],
        quotes=quotes if quotes is not None else ["Agents are central.", "Tool use is key."],
        extraction_confidence=confidence,
    )


class FakeExtractor:
    """Always returns the same canned SyllabusExtract."""

    def __init__(self, extract: SyllabusExtract) -> None:
        self._extract = extract

    async def extract_syllabus(self, page_text: str, context: str) -> SyllabusExtract:
        return self._extract


class FakeSearcher:
    """Returns a canned list of CorpusDoc on web_search_corpus."""

    def __init__(self, docs: list[CorpusDoc] | None = None) -> None:
        self._docs = docs or []

    async def web_search_corpus(self, query: str, max_results: int) -> list[CorpusDoc]:
        return self._docs


class FakeHttp:
    """Stub HTTP client: always succeeds with configurable status + body."""

    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "<html><body><p>Syllabus content</p></body></html>",
    ) -> None:
        self._status = status
        self._text = text

    async def get(self, url: str, **kwargs) -> "_FakeResponse":
        return _FakeResponse(status=self._status, text=self._text)


class _FakeResponse:
    def __init__(self, *, status: int, text: str) -> None:
        self.status_code = status
        self.text = text


class FailHttp:
    """Stub HTTP client: always raises a network error."""

    async def get(self, url: str, **kwargs) -> None:
        raise ConnectionError("simulated network failure")


async def _count_snapshots(session: AsyncSession, watch_item_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).where(SyllabusSnapshot.watch_item_id == watch_item_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# strip_html — pure function
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_drops_script_blocks():
    html = "<p>Text</p><script>alert('xss')</script><p>More</p>"
    result = strip_html(html)
    assert "alert" not in result
    assert "Text" in result
    assert "More" in result


def test_strip_html_drops_style_blocks():
    html = "<style>body { color: red; }</style><p>Content</p>"
    result = strip_html(html)
    assert "color" not in result
    assert "Content" in result


def test_strip_html_drops_noscript_blocks():
    html = "<noscript>please enable javascript</noscript><p>Text</p>"
    result = strip_html(html)
    assert "enable javascript" not in result
    assert "Text" in result


def test_strip_html_drops_svg_blocks():
    html = "<svg><rect width='10'/></svg><p>Text</p>"
    result = strip_html(html)
    assert "rect" not in result
    assert "Text" in result


def test_strip_html_drops_html_comments():
    html = "<!-- hidden comment --><p>Visible</p>"
    result = strip_html(html)
    assert "hidden" not in result
    assert "Visible" in result


def test_strip_html_decodes_common_entities():
    result = strip_html("&amp; &lt; &gt; &nbsp; &quot; &#39;")
    assert "&" in result
    assert "<" in result
    assert ">" in result
    assert '"' in result
    assert "'" in result


def test_strip_html_collapses_whitespace():
    result = strip_html("<p>  lots   of   space  </p>")
    assert "  " not in result  # no double spaces


def test_strip_html_returns_stripped_string():
    result = strip_html("  <p>Hello</p>  ")
    assert result == result.strip()


# ---------------------------------------------------------------------------
# _topic_hash — pure function
# ---------------------------------------------------------------------------


def test_topic_hash_is_deterministic():
    h1 = _topic_hash(["Agents", "RAG", "Tool Use"])
    h2 = _topic_hash(["Agents", "RAG", "Tool Use"])
    assert h1 == h2


def test_topic_hash_is_order_independent():
    h1 = _topic_hash(["Agents", "RAG", "Tool Use"])
    h2 = _topic_hash(["Tool Use", "Agents", "RAG"])
    assert h1 == h2


def test_topic_hash_is_case_insensitive():
    h1 = _topic_hash(["Agents"])
    h2 = _topic_hash(["agents"])
    assert h1 == h2


def test_topic_hash_changes_with_different_topics():
    h1 = _topic_hash(["Agents", "RAG"])
    h2 = _topic_hash(["Agents", "MCP"])
    assert h1 != h2


def test_topic_hash_empty_list_is_valid_hex():
    h = _topic_hash([])
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex = 64 chars


# ---------------------------------------------------------------------------
# check_watch_item: first run — seeds silently, returns []
# ---------------------------------------------------------------------------


async def test_first_run_seeds_snapshot_returns_empty(db_session: AsyncSession):
    """First run: one snapshot row created, no signals returned."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    extract = _make_extract(["Agents", "RAG", "Tool Use"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert signals == []
    assert await _count_snapshots(db_session, item.id) == 1


# ---------------------------------------------------------------------------
# check_watch_item: changed topics → signals + new snapshot
# ---------------------------------------------------------------------------


async def test_changed_topics_emit_one_signal_per_new_topic(db_session: AsyncSession):
    """Second run with one new topic: exactly one signal emitted."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents", "RAG"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(["Agents", "RAG", "MCP"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, PipelineSignal)
    assert sig.source_kind == "university_syllabus"
    assert "MCP" in sig.detail
    assert sig.id.startswith(f"syllabus:{item.id}:")


async def test_changed_topics_stores_new_snapshot(db_session: AsyncSession):
    """New topics: a second snapshot row is written."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents", "RAG"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(["Agents", "RAG", "MCP"])
    await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert await _count_snapshots(db_session, item.id) == 2


async def test_signal_detail_includes_topic_course_confidence_quotes(db_session: AsyncSession):
    """Signal detail contains topic name, course label, confidence, and quotes."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(
        ["Agents", "MCP"],
        confidence="high",
        quotes=["Quote one.", "Quote two.", "Quote three."],
    )
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert len(signals) == 1
    detail = signals[0].detail
    assert "MCP" in detail
    assert "CS294 Agentic AI" in detail  # course label
    assert "high" in detail  # confidence
    # Up to 2 quotes included
    assert "Quote one." in detail
    assert "Quote two." in detail
    assert "Quote three." not in detail  # only first 2


async def test_multiple_new_topics_emit_multiple_signals(db_session: AsyncSession):
    """Two new topics → two signals."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(["Agents", "MCP", "Evals"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert len(signals) == 2
    topics_in_signals = {s.id.split(":")[-1] for s in signals}
    assert len(topics_in_signals) == 2  # distinct sha1s


# ---------------------------------------------------------------------------
# check_watch_item: unchanged hash → no signals, no new snapshot
# ---------------------------------------------------------------------------


async def test_unchanged_hash_returns_empty_no_new_snapshot(db_session: AsyncSession):
    """Same topics → same hash → no signals, snapshot count unchanged."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    topics = ["Agents", "RAG"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": topics},
            raw_summary="CS294",
            content_hash=_topic_hash(topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(topics)  # same topics
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert signals == []
    assert await _count_snapshots(db_session, item.id) == 1


# ---------------------------------------------------------------------------
# check_watch_item: fetch failure (exception) → searcher fallback, search_only
# ---------------------------------------------------------------------------


async def test_fetch_exception_falls_back_to_searcher(db_session: AsyncSession):
    """Network exception on GET → searcher used, snapshot confidence=search_only."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    docs = [CorpusDoc(title="Berkeley CS294", kind="live_search", body="Agentic AI syllabus")]
    extract = _make_extract(["Agents", "Evals"])

    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(docs),
        http=FailHttp(),
    )

    # First run: seeds silently
    assert signals == []
    result = await db_session.execute(
        select(SyllabusSnapshot).where(SyllabusSnapshot.watch_item_id == item.id)
    )
    snap = result.scalar_one()
    assert snap.confidence == "search_only"


async def test_http_4xx_triggers_searcher_fallback(db_session: AsyncSession):
    """HTTP ≥400 response triggers the searcher fallback path."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    docs = [CorpusDoc(title="Search result", kind="live_search", body="Some content")]
    extract = _make_extract(["RAG"])

    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(docs),
        http=FakeHttp(status=404, text="not found"),
    )

    assert signals == []
    result = await db_session.execute(
        select(SyllabusSnapshot).where(SyllabusSnapshot.watch_item_id == item.id)
    )
    snap = result.scalar_one()
    assert snap.confidence == "search_only"


async def test_searcher_query_uses_search_hint_when_present(db_session: AsyncSession):
    """Fallback passes item.search_hint as the query when available."""
    captured_queries: list[str] = []

    class CapturingSearcher:
        async def web_search_corpus(self, query: str, max_results: int) -> list[CorpusDoc]:
            captured_queries.append(query)
            return [CorpusDoc(title="t", kind="live_search", body="body")]

    item = _make_item(search_hint="Berkeley CS294 agentic AI syllabus")
    db_session.add(item)
    await db_session.flush()

    await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(_make_extract(["Agents"])),
        searcher=CapturingSearcher(),
        http=FailHttp(),
    )

    assert captured_queries == ["Berkeley CS294 agentic AI syllabus"]


async def test_searcher_query_falls_back_to_label_when_no_hint(db_session: AsyncSession):
    """Fallback builds query from label when search_hint is None."""
    captured_queries: list[str] = []

    class CapturingSearcher:
        async def web_search_corpus(self, query: str, max_results: int) -> list[CorpusDoc]:
            captured_queries.append(query)
            return [CorpusDoc(title="t", kind="live_search", body="body")]

    item = _make_item(search_hint=None)
    db_session.add(item)
    await db_session.flush()

    await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(_make_extract(["Agents"])),
        searcher=CapturingSearcher(),
        http=FailHttp(),
    )

    assert captured_queries[0] == f"{item.label} syllabus 2026"


# ---------------------------------------------------------------------------
# check_watch_item: dry_run — signals computed, zero DB writes
# ---------------------------------------------------------------------------


async def test_dry_run_returns_signals_without_storing_snapshot(db_session: AsyncSession):
    """dry_run=True with changed topics: signals returned, no new snapshot row."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents", "RAG"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(["Agents", "RAG", "MCP"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
        dry_run=True,
    )

    assert len(signals) == 1
    assert "MCP" in signals[0].detail
    # No new snapshot stored — still 1 row (the prior)
    assert await _count_snapshots(db_session, item.id) == 1


async def test_dry_run_first_run_returns_empty_no_db_writes(db_session: AsyncSession):
    """dry_run=True on first run: returns [], writes no snapshot row."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    extract = _make_extract(["Agents", "RAG"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
        dry_run=True,
    )

    assert signals == []
    assert await _count_snapshots(db_session, item.id) == 0


# ---------------------------------------------------------------------------
# Finding 1 regression: case-normalized topic diff
# ---------------------------------------------------------------------------


async def test_case_normalized_topics_emit_one_signal(db_session: AsyncSession):
    """Prior stores ["Agents", "RAG"]; extractor returns ["agents", "rag", "MCP"].

    Exactly ONE signal should be emitted (for MCP), and its id must use
    sha1("mcp") — the lowercased form — not sha1("MCP").
    The signal detail keeps the raw extractor casing ("MCP") for display.
    """
    import hashlib

    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    prior_topics = ["Agents", "RAG"]
    db_session.add(
        SyllabusSnapshot(
            watch_item_id=item.id,
            topics={"topics": prior_topics},
            raw_summary="CS294",
            content_hash=_topic_hash(prior_topics),
            confidence="fetched",
        )
    )
    await db_session.flush()

    extract = _make_extract(["agents", "rag", "MCP"])
    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    assert len(signals) == 1, f"expected 1 signal, got {len(signals)}: {[s.id for s in signals]}"
    sig = signals[0]
    expected_sha1 = hashlib.sha1("mcp".encode()).hexdigest()
    assert sig.id == f"syllabus:{item.id}:{expected_sha1}", (
        f"id should use sha1('mcp'), got {sig.id}"
    )
    assert "MCP" in sig.detail, "raw display casing should be preserved in detail"


# ---------------------------------------------------------------------------
# Finding 3: dry_run=True + fetch-failure fallback
# ---------------------------------------------------------------------------


async def test_dry_run_fetch_failure_searcher_called_no_db_writes(db_session: AsyncSession):
    """dry_run=True + http raises: searcher IS called, returns [] (first run),
    zero snapshot rows written."""
    searcher_called = False

    class TrackingSearcher:
        async def web_search_corpus(self, query: str, max_results: int) -> list[CorpusDoc]:
            nonlocal searcher_called
            searcher_called = True
            return [CorpusDoc(title="t", kind="live_search", body="syllabus content")]

    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    signals = await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(_make_extract(["Agents"])),
        searcher=TrackingSearcher(),
        http=FailHttp(),
        dry_run=True,
    )

    assert signals == []
    assert searcher_called, "searcher must be called even when dry_run=True"
    assert await _count_snapshots(db_session, item.id) == 0


# ---------------------------------------------------------------------------
# Finding 4: first-run seed verifies stored fields
# ---------------------------------------------------------------------------


async def test_first_run_snapshot_stored_fields(db_session: AsyncSession):
    """First-run seed: snapshot row stores topics, correct content_hash, and confidence."""
    item = _make_item()
    db_session.add(item)
    await db_session.flush()

    topics = ["Agents", "RAG", "Tool Use"]
    extract = _make_extract(topics)
    await check_watch_item(
        db_session,
        item,
        extractor=FakeExtractor(extract),
        searcher=FakeSearcher(),
        http=FakeHttp(),
    )

    result = await db_session.execute(
        select(SyllabusSnapshot).where(SyllabusSnapshot.watch_item_id == item.id)
    )
    snap = result.scalar_one()
    assert snap.topics["topics"] == topics
    assert snap.content_hash == _topic_hash(topics)
    assert snap.confidence == "fetched"
