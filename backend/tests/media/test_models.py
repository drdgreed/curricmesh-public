"""Round-trip tests for MediaAsset TenantScoped model.

All tests run under DEFAULT_ORG_ID tenant context, established by the
``db_session`` fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Round-trip: MediaAsset
# ---------------------------------------------------------------------------


async def test_media_asset_round_trip(db_session: AsyncSession):
    """Insert a MediaAsset and query it back; defaults (status='pending') applied."""
    from app.models.media import MediaAsset

    asset = MediaAsset(
        kind="video",
        filename="intro-to-agents.mp4",
        mime="video/mp4",
        storage_key="00000000-0000-0000-0000-0000000d6fa1/media/test-key/intro-to-agents.mp4",
    )
    db_session.add(asset)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(MediaAsset).where(MediaAsset.id == asset.id)
        )
    ).scalar_one()

    assert row.kind == "video"
    assert row.filename == "intro-to-agents.mp4"
    assert row.mime == "video/mp4"
    assert row.storage_key == "00000000-0000-0000-0000-0000000d6fa1/media/test-key/intro-to-agents.mp4"
    assert row.status == "pending"
    assert row.size_bytes is None
    assert row.checksum is None
    assert row.duration_s is None
    assert row.created_by is None
    assert row.created_at is not None
    assert row.organization_id == DEFAULT_ORG_ID


async def test_media_asset_confirm_fields(db_session: AsyncSession):
    """Confirm fields (size_bytes, checksum, duration_s, status) can be set after creation."""
    from app.models.media import MediaAsset

    asset = MediaAsset(
        kind="audio",
        filename="lesson-narration.mp3",
        mime="audio/mpeg",
        storage_key="00000000-0000-0000-0000-0000000d6fa1/media/test-key2/lesson-narration.mp3",
    )
    db_session.add(asset)
    await db_session.flush()

    asset.size_bytes = 4_194_304
    asset.checksum = "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"
    asset.duration_s = 95.4
    asset.status = "ready"
    await db_session.commit()

    row = (
        await db_session.execute(
            select(MediaAsset).where(MediaAsset.id == asset.id)
        )
    ).scalar_one()

    assert row.status == "ready"
    assert row.size_bytes == 4_194_304
    assert row.checksum == "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"
    assert row.duration_s == pytest.approx(95.4)
    assert row.organization_id == DEFAULT_ORG_ID
