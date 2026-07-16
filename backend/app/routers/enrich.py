"""Router: POST /api/v1/ccrs/{ccr_id}/enrich — attach AI placement + draft frame to a gap CCR.

Architect / program_manager only. Advisory only: never mutates curriculum or CCR
status. The AI client is built lazily (and only if configured), so production
stays safe and tests can override the dependency with a fake enricher.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, GapEnricher
from app.ai.enricher import enrich_ccr
from app.auth.rbac import require_roles
from app.config import settings
from app.database import get_db
from app.schemas.workflow import CCROut

router = APIRouter(prefix="/api/v1/ccrs", tags=["enrich"])

_ENRICH_ROLES = require_roles("architect", "program_manager")


def get_ai_enricher() -> GapEnricher:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency with a fake enricher.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI enrichment is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


@router.post("/{ccr_id}/enrich", response_model=CCROut)
async def enrich(
    ccr_id: uuid.UUID,
    current: dict[str, Any] = Depends(_ENRICH_ROLES),
    db: AsyncSession = Depends(get_db),
    enricher: GapEnricher = Depends(get_ai_enricher),
) -> CCROut:
    try:
        ccr = await enrich_ccr(db, ccr_id=ccr_id, enricher=enricher)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    await db.refresh(ccr)
    return CCROut.model_validate(ccr)
