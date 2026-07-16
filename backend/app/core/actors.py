"""System DB-actor utilities.

Get-or-create helpers for the non-human actors CurricMesh writes rows as.
Lives in ``app.core`` (not ``app.ai``) because it is a plain DB utility — not
AI-specific — so AI modules can depend on it without forming an import cycle
(client → qa_judge → actors, with no edge back into client).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

_AI_RESEARCHER_EMAIL = "ai-researcher@curricmesh.system"
_AI_RESEARCHER_NAME = "AI Researcher"
# Dedicated low-privilege role: the AI actor never makes authenticated requests,
# so giving it an elevated human role (e.g. program_manager) would be a latent
# privilege risk if any future code resolved a user by email and trusted the DB
# role column.
_AI_RESEARCHER_ROLE = "system"


async def ensure_ai_researcher(session: AsyncSession) -> User:
    """Get-or-create the system AI Researcher actor. Idempotent."""
    result = await session.execute(
        select(User).where(User.email == _AI_RESEARCHER_EMAIL)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        email=_AI_RESEARCHER_EMAIL,
        display_name=_AI_RESEARCHER_NAME,
        role=_AI_RESEARCHER_ROLE,
    )
    session.add(user)
    await session.flush()
    return user


async def get_ai_researcher(session: AsyncSession) -> User | None:
    """Resolve the system AI Researcher actor, or None if it does not exist.

    Read-only counterpart to ``ensure_ai_researcher``: callers that merely want
    to *find* AI-authored rows (e.g. the AI-findings inbox) must not create the
    actor as a side effect of a GET.
    """
    result = await session.execute(
        select(User).where(User.email == _AI_RESEARCHER_EMAIL)
    )
    return result.scalar_one_or_none()
