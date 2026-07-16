"""University watchlist adapter for the freshness pipeline.

Implements the fetch → extract → snapshot-diff → signals algorithm described
in the Phase 1 plan (Task 5).  The public entry point is ``check_watch_item``.

Algorithm (spec-binding):
1. GET ``item.url`` with a custom UA, 25 s timeout, follow_redirects.
   HTTP ≥ 400 or any exception → fall back to
   ``searcher.web_search_corpus(search_hint or "{label} syllabus 2026", 5)``,
   join doc bodies as pseudo-page, mark ``confidence="search_only"``.
2. ``extractor.extract_syllabus(page_text[:35 000], context=…)``
3. Load latest ``SyllabusSnapshot`` for the item.
   - No prior → store one, return ``[]`` (silent first-run seed).
   - Same ``content_hash`` → return ``[]`` (no duplicate row).
4. Else: new topics = set difference vs prior; emit one ``PipelineSignal``
   per new topic; store the new snapshot; return signals.

``dry_run=True``: skips ALL DB writes while still returning what WOULD be new.
The runner (Task 7) uses this mode.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import SyllabusExtract
from app.freshness_pipeline import PipelineSignal
from app.models.freshness_pipeline import SourceWatchItem, SyllabusSnapshot

logger = logging.getLogger(__name__)

_UA = "CurricMeshFreshness/0.1 (+curriculum research)"
_TIMEOUT = 25.0
_MAX_PAGE_CHARS = 35_000


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def strip_html(html: str) -> str:
    """Strip HTML to plain text via a regex pipeline (no parser dependency).

    Pipeline (order matters):
    1. Drop block elements with their content:
       ``<script>``, ``<style>``, ``<noscript>``, ``<svg>`` (incl. DOTALL).
    2. Drop HTML comments (``<!-- … -->``).
    3. Drop all remaining tags.
    4. Decode common HTML entities.
    5. Collapse whitespace and strip leading/trailing.
    """
    for tag in ("script", "style", "noscript", "svg"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    html = re.sub(r"<[^>]+>", " ", html)
    html = (
        html.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", html).strip()


def _topic_hash(topics: list[str]) -> str:
    """SHA-256 of newline-joined, lowercased, sorted topics.

    Produces a 64-character hex string.  Two topic lists that differ only in
    case or ordering produce the same hash (idempotent diff guard).
    """
    normalised = sorted(t.lower() for t in topics)
    payload = "\n".join(normalised).encode()
    return hashlib.sha256(payload).hexdigest()


def _sha1_hex(s: str) -> str:
    """SHA-1 hex digest of a string (used for per-topic signal IDs)."""
    return hashlib.sha1(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


async def check_watch_item(
    session: AsyncSession,
    item: SourceWatchItem,
    *,
    extractor: Any,  # SyllabusExtractor protocol
    searcher: Any,  # WebSearcher protocol
    http: Any,  # httpx.AsyncClient or duck-typed stub
    dry_run: bool = False,
) -> list[PipelineSignal]:
    """Fetch, extract, diff, and emit signals for one ``SourceWatchItem``.

    Parameters
    ----------
    session:
        AsyncSession already operating under the correct org context
        (``use_org`` established by the caller — TenantScoped writes stamp
        ``organization_id`` automatically).
    item:
        The watch item to process.
    extractor:
        SyllabusExtractor protocol — ``extract_syllabus(page_text, context)``.
    searcher:
        WebSearcher protocol — ``web_search_corpus(query, max_results)``.
    http:
        An httpx.AsyncClient (or compatible duck-type) for the initial GET.
    dry_run:
        If True, skip all DB writes but still compute and return signals.
    """
    # ------------------------------------------------------------------
    # Step 1: Fetch the page; fall back to web search on failure.
    # ------------------------------------------------------------------
    confidence = "fetched"
    page_text = ""

    try:
        resp = await http.get(
            item.url,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code >= 400:
            raise ValueError(f"HTTP {resp.status_code} for {item.url}")
        page_text = strip_html(resp.text)
    except Exception as exc:
        logger.warning(
            "fetch failed for '%s' (%s): %s — falling back to web search",
            item.label,
            item.url,
            exc,
        )
        query = item.search_hint or f"{item.label} syllabus 2026"
        docs = await searcher.web_search_corpus(query, 5)
        page_text = "\n\n".join(doc.body for doc in docs)
        confidence = "search_only"

    # ------------------------------------------------------------------
    # Step 2: Extract syllabus structure.
    # ------------------------------------------------------------------
    extract: SyllabusExtract = await extractor.extract_syllabus(
        page_text[:_MAX_PAGE_CHARS],
        context=f"{item.institution} — {item.label}",
    )
    new_hash = _topic_hash(extract.topics)

    # ------------------------------------------------------------------
    # Step 3: Load the latest snapshot for this watch item.
    # ------------------------------------------------------------------
    result = await session.execute(
        select(SyllabusSnapshot)
        .where(SyllabusSnapshot.watch_item_id == item.id)
        .order_by(desc(SyllabusSnapshot.captured_at), desc(SyllabusSnapshot.id))
        .limit(1)
    )
    prior = result.scalar_one_or_none()

    if prior is None:
        # First run: seed silently — no signals.
        if not dry_run:
            session.add(
                SyllabusSnapshot(
                    watch_item_id=item.id,
                    topics={"topics": extract.topics},
                    raw_summary=extract.course_title,
                    content_hash=new_hash,
                    confidence=confidence,
                )
            )
            await session.flush()
        return []

    # Unchanged content: short-circuit without duplicating the snapshot row.
    if prior.content_hash == new_hash:
        return []

    # ------------------------------------------------------------------
    # Step 4: Diff topics and emit signals.
    # ------------------------------------------------------------------
    # Normalise to lowercase for identity comparison so "Agents" and "agents"
    # are treated as the same topic across runs.  Raw casing is kept in
    # new_topics (and later in detail/title) for human-readable display.
    prior_topics: set[str] = {t.lower() for t in prior.topics.get("topics", [])}
    seen_lower: set[str] = set()
    new_topics: list[str] = []
    for t in extract.topics:
        tl = t.lower()
        if tl not in prior_topics and tl not in seen_lower:
            new_topics.append(t)   # preserve raw casing for display
            seen_lower.add(tl)

    now = datetime.now(tz=timezone.utc).isoformat()
    signals: list[PipelineSignal] = []

    for topic in new_topics:
        topic_id = f"syllabus:{item.id}:{_sha1_hex(topic.lower())}"
        quotes = extract.quotes[:2]
        detail_parts = [
            f"New topic: {topic}",
            f"Course: {item.label} ({item.institution})",
            f"Confidence: {extract.extraction_confidence} (fetch: {confidence})",
        ]
        if quotes:
            detail_parts.append("Quotes: " + " | ".join(quotes))
        signals.append(
            PipelineSignal(
                id=topic_id,
                source_kind="university_syllabus",
                source=item.institution,
                title=f"New topic in {item.label}: {topic}",
                url=item.url,
                detail="\n".join(detail_parts),
                captured_at=now,
            )
        )

    # Store the new snapshot (unless dry run).
    if not dry_run:
        session.add(
            SyllabusSnapshot(
                watch_item_id=item.id,
                topics={"topics": extract.topics},
                raw_summary=extract.course_title,
                content_hash=new_hash,
                confidence=confidence,
            )
        )
        await session.flush()

    return signals
