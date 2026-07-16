"""Router: POST /api/v1/curricula/{id}/research — trigger the SOTA-gap researcher.

Architect / program_manager only. Drafts a CCR per genuine industry gap via the
normal workflow. The Anthropic client is built lazily (and only if configured),
so production stays safe and tests can override the dependency with a fake.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, GapExtractor
from app.ai.corpus import CorpusProvider, CuratedCorpusProvider, LiveCorpusProvider
from app.ai.sota_researcher import analyze_gaps
from app.auth.rbac import require_roles
from app.config import settings
from app.database import get_db
from app.models.curriculum import Curriculum
from app.models.version import Version
from app.schemas.workflow import CCROut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/curricula", tags=["research"])

_RESEARCH_ROLES = require_roles("architect", "program_manager")


def get_ai_extractor() -> GapExtractor:
    """Lazily build the real AI client; 503 if the API key is not configured.

    Tests override this dependency with a fake extractor.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI research is not configured (ANTHROPIC_API_KEY missing)",
        )
    return AIClient(api_key=settings.ANTHROPIC_API_KEY)


def get_corpus_provider(
    live: bool = False,
    extractor: GapExtractor = Depends(get_ai_extractor),
) -> CorpusProvider:
    """Select the corpus source. Default = curated (offline, deterministic).

    ``?live=true`` opts a single run into the live web-search provider — gated on
    ``LIVE_SOTA_ENABLED``. The API-key gate already runs via ``get_ai_extractor``
    (which 503s without a key and which ``research_gaps`` also depends on), so a
    keyless request 503s before reaching here. FastAPI's per-request dependency
    cache makes the injected ``extractor`` the SAME ``AIClient`` instance the
    router reuses — and ``AIClient`` satisfies the ``WebSearcher`` seam. Tests
    override this dependency with a fake (live) provider.
    """
    if not live:
        return CuratedCorpusProvider()
    if not settings.LIVE_SOTA_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Live SOTA research is not enabled (LIVE_SOTA_ENABLED)",
        )
    return LiveCorpusProvider(extractor, settings.LIVE_SOTA_MAX_RESULTS)


async def _resolve_version(db: AsyncSession, curriculum: Curriculum) -> Version | None:
    """Resolve the version to analyze: the active (current) version, else latest."""
    if curriculum.current_version_id is not None:
        result = await db.execute(
            select(Version).where(Version.id == curriculum.current_version_id)
        )
        version = result.scalar_one_or_none()
        if version is not None:
            return version

    # Fall back to the latest version by semver.
    result = await db.execute(
        select(Version)
        .where(Version.curriculum_id == curriculum.id)
        .order_by(Version.major.desc(), Version.minor.desc(), Version.patch.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/{curriculum_id}/research", response_model=list[CCROut], status_code=201)
async def research_gaps(
    curriculum_id: uuid.UUID,
    current: dict[str, Any] = Depends(_RESEARCH_ROLES),
    db: AsyncSession = Depends(get_db),
    extractor: GapExtractor = Depends(get_ai_extractor),
    provider: CorpusProvider = Depends(get_corpus_provider),
) -> list[CCROut]:
    cur_result = await db.execute(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    curriculum = cur_result.scalar_one_or_none()
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    version = await _resolve_version(db, curriculum)
    if version is None:
        raise HTTPException(status_code=400, detail="Curriculum has no version to analyze")

    corpus = await provider.fetch(db, curriculum)
    if not corpus:
        raise HTTPException(status_code=400, detail="No SOTA corpus loaded")

    ccrs = await analyze_gaps(
        db,
        curriculum_id=curriculum_id,
        version=version,
        corpus=corpus,
        extractor=extractor,
    )
    await db.commit()
    return [CCROut.model_validate(c) for c in ccrs]
