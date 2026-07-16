"""Shared seeding helpers for the retrieval-infra tests."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.models.media import MediaAsset, MediaTranscript


async def seed_version_with_members(
    session: AsyncSession, *, texts: list[str]
) -> CurriculumVersion:
    """Create a curriculum version whose members carry ``texts`` as content.

    One LineageAsset + ContentVersion + VersionMember per entry in ``texts``.
    Returns the persisted (flushed) ``CurriculumVersion``.
    """
    cur = Curriculum(name="AI Eng", slug=f"ai-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    version = CurriculumVersion(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    session.add(version)
    await session.flush()

    for i, body in enumerate(texts):
        asset = LineageAsset(
            kind=AssetKind.lesson_plan, lineage_key=f"wk{i:02d}/lesson_plan"
        )
        session.add(asset)
        await session.flush()

        cv = ContentVersion(
            asset_id=asset.id,
            seq=1,
            content=body,
            content_hash=content_hash("lesson_plan", body, {}),
        )
        session.add(cv)
        await session.flush()

        session.add(
            VersionMember(
                curriculum_version_id=version.id,
                asset_id=asset.id,
                asset_version_id=cv.id,
                section="Week 1",
                week_index=i,
                order=i,
            )
        )
    await session.flush()
    return version


async def seed_media_asset(
    session: AsyncSession, *, transcript_text: str | None = None
) -> MediaAsset:
    """Create a ready ``MediaAsset``, optionally with a one-per-asset transcript.

    ``transcript_text=None`` seeds an asset with NO transcript (to prove media
    without a transcript is handled gracefully — no media chunks).
    """
    asset = MediaAsset(
        kind="video",
        filename=f"lecture-{uuid.uuid4().hex[:8]}.mp4",
        mime="video/mp4",
        storage_key=f"media/{uuid.uuid4().hex}.mp4",
        status="ready",
        duration_s=42.0,
    )
    session.add(asset)
    await session.flush()

    if transcript_text is not None:
        session.add(
            MediaTranscript(
                media_asset_id=asset.id,
                text=transcript_text,
                language="en",
                provider="whisper-test",
            )
        )
        await session.flush()
    return asset


def media_ref(asset: MediaAsset, order_index: int = 0) -> dict:
    """A frozen ``media_refs`` entry mirroring ``compile._asset_ref``.

    The asset id lives under ``media_asset_id`` (the key ``compile.py`` writes) —
    ingestion resolves the transcript from it.
    """
    return {
        "media_asset_id": str(asset.id),
        "storage_key": asset.storage_key,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime": asset.mime,
        "duration_s": asset.duration_s,
        "order_index": order_index,
    }


async def seed_version_with_media(
    session: AsyncSession,
    *,
    item_text: str,
    media_refs: list[dict] | None,
) -> tuple[CurriculumVersion, VersionMember]:
    """Create a one-member version whose ``ContentVersion`` pins ``media_refs``.

    Returns the (version, member). ``media_refs`` is frozen onto the member's
    ``ContentVersion`` exactly as ``publish`` would, so ingestion can walk
    member → ``ContentVersion.media_refs`` → ``MediaAsset`` id → transcript.
    """
    cur = Curriculum(name="AI Eng", slug=f"ai-{uuid.uuid4().hex[:8]}")
    session.add(cur)
    await session.flush()

    version = CurriculumVersion(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
    )
    session.add(version)
    await session.flush()

    asset = LineageAsset(kind=AssetKind.lesson_plan, lineage_key="wk00/lesson_plan")
    session.add(asset)
    await session.flush()

    cv = ContentVersion(
        asset_id=asset.id,
        seq=1,
        content=item_text,
        content_hash=content_hash("lesson_plan", item_text, {}),
        media_refs=media_refs,
    )
    session.add(cv)
    await session.flush()

    member = VersionMember(
        curriculum_version_id=version.id,
        asset_id=asset.id,
        asset_version_id=cv.id,
        section="Week 1",
        week_index=0,
        order=0,
    )
    session.add(member)
    await session.flush()
    return version, member
