"""Ingestion — build a released version's retrieval index (Phase B, Task 3).

``ingest_version`` chunks every member's item text, embeds the chunks, and
writes ``ContentChunk`` rows pinned to the version. It is the hook the Phase-1
design reserved ("because we hold the bytes, a later pipeline … indexes"); the
release-event wiring that *calls* it on activation is a LATER convergence step,
not this build. This module exposes the callable + a thin admin trigger.

Guarantees:
* **Version + tenant scoped.** Chunks carry ``curriculum_version_id`` and
  (via ``TenantScoped``) ``organization_id`` write-stamped from the ambient org
  context. All reads/writes here run under the caller's tenant context.
* **Idempotent per version.** Ingestion first deletes any existing chunks for
  the version, then rebuilds — so re-running converges to the same set rather
  than duplicating. A released version is immutable, so its rebuilt index is
  identical.
* **Text source.** Each ``VersionMember`` resolves to its pinned
  ``ContentVersion.content`` (the immutable body), indexed as ``kind="text"``.
* **Media-transcript source.** A member's item can embed media; the release
  froze those pins onto ``ContentVersion.media_refs`` (each entry carries the
  ``MediaAsset`` id under ``media_asset_id`` — see ``builder/compile._asset_ref``).
  For every referenced asset that HAS a ``MediaTranscript`` (one-per-asset), its
  transcript text is chunked + embedded into ``ContentChunk(kind=
  "media_transcript")`` pinned to the SAME member — so the tutor retrieves over
  video/audio, not just text. An asset with no transcript is skipped gracefully.
  These chunks share the version's delete-then-rebuild, so ingestion stays
  idempotent across both kinds.

No real embedding API is used in tests — inject a ``FakeEmbedder``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.chunker import chunk_text
from app.core.retrieval.embedder import Embedder
from app.models.content_model import ContentVersion, VersionMember
from app.models.media import MediaTranscript
from app.models.retrieval import ContentChunk


def _media_asset_ids(media_refs: object) -> list[uuid.UUID]:
    """Extract the ordered, de-duplicated ``MediaAsset`` ids from ``media_refs``.

    Tolerates malformed entries (non-dict, missing/garbage ``media_asset_id``)
    the way ``learn._present_media`` tolerates malformed refs — a bad ref is
    skipped, never a hard failure.
    """
    ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    if not isinstance(media_refs, list):
        return ids
    for ref in media_refs:
        if not isinstance(ref, dict):
            continue
        raw = ref.get("media_asset_id")
        if not raw:
            continue
        try:
            asset_id = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            continue
        if asset_id in seen:
            continue
        seen.add(asset_id)
        ids.append(asset_id)
    return ids


async def ingest_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    embedder: Embedder,
) -> int:
    """(Re)build the retrieval index for one curriculum version.

    Returns the number of ``ContentChunk`` rows written. Idempotent: any
    existing chunks for ``version_id`` are deleted before the rebuild.
    The caller commits (this function only flushes, so it composes inside a
    larger release transaction).
    """
    # 1. Idempotency: clear the version's existing index. The app-layer tenant
    #    filter + RLS keep this scoped to the caller's org.
    await session.execute(
        delete(ContentChunk).where(
            ContentChunk.curriculum_version_id == version_id
        )
    )

    # 2. Resolve each member to its pinned immutable content body + frozen media
    #    pins (the media assets the item embeds, snapshotted at release).
    rows = (
        await session.execute(
            select(
                VersionMember,
                ContentVersion.content,
                ContentVersion.media_refs,
            )
            .join(
                ContentVersion,
                ContentVersion.id == VersionMember.asset_version_id,
            )
            .where(VersionMember.curriculum_version_id == version_id)
            .order_by(VersionMember.order)
        )
    ).all()

    # 3. Chunk item text. Collect each member's referenced media asset ids so we
    #    can resolve their transcripts in a single query below.
    pending: list[tuple[VersionMember, str, int, str]] = []  # (member, text, tok, kind)
    member_media: list[tuple[VersionMember, list[uuid.UUID]]] = []
    all_media_ids: set[uuid.UUID] = set()
    for member, content, media_refs in rows:
        for chunk in chunk_text(content or ""):
            pending.append((member, chunk.text, chunk.token_count, "text"))
        ids = _media_asset_ids(media_refs)
        if ids:
            member_media.append((member, ids))
            all_media_ids.update(ids)

    # 3b. Resolve transcripts (one-per-asset) for every referenced asset in one
    #     tenant-scoped query, then chunk each into the SAME member that embeds
    #     it. An asset with no transcript is simply absent from the map → skipped.
    if all_media_ids:
        transcript_rows = (
            await session.execute(
                select(MediaTranscript.media_asset_id, MediaTranscript.text).where(
                    MediaTranscript.media_asset_id.in_(all_media_ids)
                )
            )
        ).all()
        transcripts = {asset_id: txt for asset_id, txt in transcript_rows}
        for member, ids in member_media:
            for asset_id in ids:
                txt = transcripts.get(asset_id)
                if not txt:
                    continue  # asset without a transcript — graceful skip
                for chunk in chunk_text(txt):
                    pending.append(
                        (member, chunk.text, chunk.token_count, "media_transcript")
                    )

    if not pending:
        return 0

    vectors = await embedder.embed([text for _, text, _, _ in pending])

    # 4. Write the ContentChunk rows (organization_id auto-stamped).
    for (member, text, token_count, kind), embedding in zip(pending, vectors):
        session.add(
            ContentChunk(
                curriculum_version_id=version_id,
                source_member_id=member.id,
                kind=kind,
                text=text,
                embedding=embedding,
                token_count=token_count,
            )
        )
    await session.flush()
    return len(pending)
