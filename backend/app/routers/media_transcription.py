"""Router: thin admin trigger for the media transcription pipeline (Phase B, B2).

POST /api/v1/media/{asset_id}/transcribe — transcribe (AV) or extract text
(pdf/doc) for a ready asset and store the ``MediaTranscript``. ``image`` /
``other`` assets are a clean skip.

Writer-tier only (architect / program_manager), matching the media library's
write endpoints. Both the storage adapter (``get_storage``) and the transcriber
(``get_transcriber``) return 503 when unconfigured — the dependency raises
before the endpoint body runs.

This is a thin trigger over ``app.core.transcription.transcribe_asset`` (the
reusable callable); background/batch callers use that function directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.core.transcription import (
    MediaAssetNotFound,
    TranscriptionError,
    transcribe_asset,
)
from app.database import get_db
from app.media.storage import StorageBackend, get_storage
from app.media.transcription import Transcriber, get_transcriber

router = APIRouter(prefix="/api/v1/media", tags=["media"])

_WRITE_ROLES = require_roles("architect", "program_manager")


class MediaTranscriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    media_asset_id: uuid.UUID
    text: str
    language: str | None
    provider: str
    created_at: datetime


class TranscribeResponse(BaseModel):
    """Result of a transcribe trigger.

    ``status`` is ``"transcribed"`` when a transcript was produced/replaced, or
    ``"skipped"`` for non-transcribable kinds (image/other) — in which case
    ``transcript`` is ``None``.
    """

    status: Literal["transcribed", "skipped"]
    transcript: MediaTranscriptOut | None = None


@router.post("/{asset_id}/transcribe", response_model=TranscribeResponse)
async def transcribe_media(
    asset_id: uuid.UUID,
    force: bool = Query(
        default=False,
        description="Re-transcribe and replace an existing transcript.",
    ),
    current: dict[str, Any] = Depends(_WRITE_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    transcriber: Transcriber = Depends(get_transcriber),
) -> TranscribeResponse:
    """Transcribe / extract text for an asset and store the transcript.

    404 if the asset is not in the caller's org; 400 if it is not ready or its
    bytes cannot be extracted; 503 if storage or transcription is unconfigured.
    """
    try:
        transcript = await transcribe_asset(
            db, asset_id, transcriber, storage, force=force
        )
    except MediaAssetNotFound:
        raise HTTPException(status_code=404, detail="Media asset not found")
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if transcript is None:
        return TranscribeResponse(status="skipped", transcript=None)
    return TranscribeResponse(
        status="transcribed",
        transcript=MediaTranscriptOut.model_validate(transcript),
    )
