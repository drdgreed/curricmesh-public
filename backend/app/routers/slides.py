"""Router: render a deck.md → PDF/PPTX/HTML and store it in R2 (S1 trigger).

POST /api/v1/slides/render — render + store a supplied ``deck.md`` (+ optional
mermaid diagrams) and return the artifact storage keys + presigned download URLs.

This is the S1 "trigger": no AI generation (that is S2), no player UI, no
release hook (later slices). Writes are architect/program_manager only, mirroring
the media library's write tier. Storage disabled (STORAGE_BUCKET unset) → 503 via
the get_storage() dependency.

Rendering shells out to marp/mermaid (Node + Chromium), so it is offloaded to a
threadpool to keep the event loop free. It is synchronous from the caller's view;
a background-job/async variant is a later concern once decks get large.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_roles
from app.database import get_db
from app.media.storage import StorageBackend, get_storage
from app.models.deck_artifact import DeckArtifact
from app.slides.qa import DeckQAReport, qa_deck
from app.slides.render import RenderError
from app.slides.store import render_and_store_deck
from app.tenant import require_org

router = APIRouter(prefix="/api/v1/slides", tags=["slides"])

_SLIDES_WRITE_ROLES = require_roles("architect", "program_manager")
# QA is read-only analysis, so it opens to the full deck-author tier (mirrors
# authoring_ai.py's _AUTHOR_ROLES) rather than the narrower render/write tier.
_SLIDES_AUTHOR_ROLES = require_roles(
    "instructor", "instructor_lead", "architect", "program_manager"
)


class RenderDeckRequest(BaseModel):
    deck_md: str = Field(min_length=1, description="Marp markdown deck source.")
    diagrams: dict[str, str] = Field(
        default_factory=dict,
        description="Optional {name: mermaid_source}; rendered to ../diagrams/<stem>.png.",
    )


class RenderDeckResponse(BaseModel):
    pdf_key: str
    pptx_key: str
    html_key: str
    pdf_url: str
    pptx_url: str
    html_url: str


class DeckPreview(BaseModel):
    """An author's preview of a linked deck (fresh presigned GET URLs)."""

    id: uuid.UUID
    curriculum_version_id: uuid.UUID
    source_member_id: uuid.UUID | None
    status: str
    created_at: datetime
    html_url: str
    pdf_url: str
    pptx_url: str


@router.post("/render", response_model=RenderDeckResponse)
async def render_slide_deck(
    body: RenderDeckRequest,
    current: dict[str, Any] = Depends(_SLIDES_WRITE_ROLES),  # noqa: ARG001
    storage: StorageBackend = Depends(get_storage),
) -> RenderDeckResponse:
    """Render + store a deck; return artifact keys and presigned download URLs.

    The storage key prefix is tenant-scoped and unique per render:
    ``decks/<org>/<uuid>/``.
    """
    org_id = require_org()
    key_prefix = f"decks/{org_id}/{uuid.uuid4()}"
    try:
        result = await run_in_threadpool(
            render_and_store_deck,
            storage,
            key_prefix,
            body.deck_md,
            diagrams=body.diagrams,
            tenant=str(org_id),
        )
    except RenderError as exc:
        # A render failure is a bad-deck / toolchain problem, not a server bug the
        # caller can't act on — surface it as 502 with the clear message.
        raise HTTPException(status_code=502, detail=f"deck render failed: {exc}") from exc
    return RenderDeckResponse(**result)


@router.get("/versions/{version_id}/decks", response_model=list[DeckPreview])
async def preview_version_decks(
    version_id: uuid.UUID,
    current: dict[str, Any] = Depends(_SLIDES_WRITE_ROLES),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> list[DeckPreview]:
    """Author preview: the decks linked to a ``CurriculumVersion`` (this tenant).

    Lets an architect/program_manager review a version's decks (with fresh
    presigned GET URLs) before or after release, without enrolling as a learner.
    Tenant-scoped by the ORM auto-filter — another tenant's version yields an
    empty list, never a leak.
    """
    stmt = (
        select(DeckArtifact)
        .where(DeckArtifact.curriculum_version_id == version_id)
        .order_by(DeckArtifact.created_at)
    )
    decks = (await db.execute(stmt)).scalars().all()
    return [
        DeckPreview(
            id=d.id,
            curriculum_version_id=d.curriculum_version_id,
            source_member_id=d.source_member_id,
            status=d.status,
            created_at=d.created_at,
            html_url=storage.presigned_get_url(d.html_key),
            pdf_url=storage.presigned_get_url(d.pdf_key),
            pptx_url=storage.presigned_get_url(d.pptx_key),
        )
        for d in decks
    ]


# ---------------------------------------------------------------------------
# Deck QA gate (S3) — mechanical rubric + human-review flags.
# ---------------------------------------------------------------------------


class QADeckRequest(BaseModel):
    deck_md: str = Field(min_length=1, description="Marp markdown deck source to QA.")


class QAGateOut(BaseModel):
    name: str
    status: str  # "pass" | "fail" | "needs_human"
    detail: str


class QADeckResponse(BaseModel):
    passed: bool
    gates: list[QAGateOut]


@router.post("/qa", response_model=QADeckResponse)
async def qa_slide_deck(
    body: QADeckRequest,
    current: dict[str, Any] = Depends(_SLIDES_AUTHOR_ROLES),  # noqa: ARG001
) -> QADeckResponse:
    """Run the deck QA gate over a supplied ``deck.md`` and return the report.

    Stateless analysis — no storage, no DB, nothing persisted. ``passed`` is the
    mechanical verdict; a deck is release-ready only when ``passed`` AND the
    ``needs_human`` gates are cleared by a reviewer (see
    ``app.slides.qa.deck_ready_to_ship`` — the deck-specific check that plugs
    into the mandatory QA→release model, mirroring the course QA gate).
    """
    report: DeckQAReport = qa_deck(body.deck_md)
    return QADeckResponse(
        passed=report.passed,
        gates=[QAGateOut(name=g.name, status=g.status, detail=g.detail) for g in report.gates],
    )
