"""Router: admin trigger for retrieval ingestion (Phase B, Task 3).

A THIN operator trigger to (re)build a curriculum version's retrieval index on
demand:

POST /api/v1/admin/retrieval/versions/{version_id}/ingest
    → chunk + embed + write ContentChunk rows for the version. Idempotent.

This is a manual/admin hook only. The automatic release-event wiring (ingest on
version activation) is a LATER convergence step, not this build — so ingestion
is exercisable now without coupling to the release path yet.

Manager-tier (architect / program_manager), mirroring the media-write RBAC.
Embedder is chosen by config (``get_embedder``): the FakeEmbedder in dev/CI, a
governed hosted embedder in a real deployment.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.core.retrieval.embedder import get_embedder
from app.core.retrieval.ingest import ingest_version
from app.database import get_db
from app.models.content_model import CurriculumVersion

router = APIRouter(prefix="/api/v1/admin/retrieval", tags=["retrieval-admin"])

_INGEST_ROLES = require_roles("architect", "program_manager")


class IngestResponse(BaseModel):
    version_id: uuid.UUID
    chunks_written: int


@router.post(
    "/versions/{version_id}/ingest", response_model=IngestResponse, status_code=201
)
async def trigger_ingest(
    version_id: uuid.UUID,
    current: dict[str, Any] = Depends(_INGEST_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    """(Re)build the retrieval index for ``version_id`` (idempotent).

    404 if the version is not found in the caller's org (cross-org versions are
    invisible via the ORM tenant-scope filter).
    """
    version = (
        await db.execute(
            select(CurriculumVersion).where(CurriculumVersion.id == version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Curriculum version not found")

    written = await ingest_version(db, version_id, get_embedder())
    await db.commit()
    return IngestResponse(version_id=version_id, chunks_written=written)
