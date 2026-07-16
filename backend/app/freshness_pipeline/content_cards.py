"""Content-card builder for freshness-pipeline detection (Phase 3 Part A).

``build_content_cards`` compresses the curriculum's active immutable content
model into a flat list of lightweight dicts — one per member asset — that the
gap-extraction prompt can reason over without seeing full asset bodies.

Card shape (exact keys):
  lineage_key   str    — stable asset key (``LineageAsset.lineage_key``)
  kind          str    — ``AssetKind.value`` (e.g. "lesson_plan")
  section       str    — placement section (``VersionMember.section``)
  week_index    int    — placement week (``VersionMember.week_index``)
  first_line    str    — first non-empty line of the content body
  excerpt       str    — first 400 chars of whitespace-collapsed body
  headings      list   — lines starting with "## " (max 12), prefix stripped
  word_count    int    — ``len(body.split())``

Order is deterministic: ``(week_index, order)``, then ``lineage_key``
tiebreak (mirrors ``version_members`` in ``app/core/manifest.py``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.content_model import ContentVersion, LineageAsset, VersionMember

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.curriculum import Curriculum

logger = logging.getLogger(__name__)


def _build_card(member: VersionMember, content: ContentVersion, lineage: LineageAsset) -> dict:
    """Build a single content card from the three joined rows."""
    body: str = content.content

    # Whitespace-collapsed excerpt: collapse any run of whitespace to a single
    # space, then take the first 400 characters.
    collapsed = " ".join(body.split())
    excerpt = collapsed[:400]

    # first_line: first non-empty line of the raw body.
    first_line = next((ln for ln in body.splitlines() if ln.strip()), "")

    # headings: lines starting with "## " (ATX h2), prefix stripped, max 12.
    headings = [ln[3:] for ln in body.splitlines() if ln.startswith("## ")][:12]

    # word_count: token count on the raw body.
    word_count = len(body.split())

    return {
        "lineage_key": lineage.lineage_key,
        "kind": lineage.kind.value,
        "section": member.section,
        "week_index": member.week_index,
        "first_line": first_line,
        "excerpt": excerpt,
        "headings": headings,
        "word_count": word_count,
    }


async def build_content_cards(
    session: "AsyncSession",
    curriculum: "Curriculum",
) -> list[dict] | None:
    """Return content cards for ``curriculum``'s active immutable version.

    Returns ``None`` when ``curriculum.active_content_version_id`` is ``None``
    (no immutable model present yet — caller degrades to Phase-2 detection).

    The query mirrors ``app/core/manifest.py::version_members`` (same 3-way
    join: ``VersionMember → ContentVersion → LineageAsset``) but fetches
    ``ContentVersion.content`` directly — ``version_members`` returns only
    ``content_version_id``, not the body text, so importing it would require
    a second round-trip.
    """
    if curriculum.active_content_version_id is None:
        return None

    rows = (
        await session.execute(
            select(VersionMember, ContentVersion, LineageAsset)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == curriculum.active_content_version_id)
        )
    ).all()

    # Deterministic order: (week_index, order, lineage_key) — mirrors manifest.
    rows.sort(key=lambda r: (r[0].week_index, r[0].order, r[2].lineage_key))

    return [_build_card(member, content, lineage) for member, content, lineage in rows]
