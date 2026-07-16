"""Auth router: login (POST /api/v1/auth/login) and me (GET /api/v1/auth/me)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.auth.passwords import hash_password, verify_password
from app.auth.rbac import get_current_user
from app.database import get_db
from app.models.org import Organization
from app.models.user import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Computed once at module load; used as a constant-time fallback so that
# unknown-email requests still pay the full bcrypt cost (timing-oracle fix).
_DUMMY_HASH = hash_password("invalid-credentials-placeholder")


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    sub: str
    role: str
    org: str | None = None
    org_name: str | None = None


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email + password; return a JWT access token."""
    # Normalize the email — trim whitespace + lowercase — so browser
    # auto-capitalization (e.g. "Architect@…" on iOS/Safari) or a stray trailing
    # space doesn't reject valid credentials. Stored emails are lowercase; the
    # password is verified verbatim (never trimmed/normalized).
    email = body.email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user: User | None = result.scalar_one_or_none()

    # Always run verify_password — even for unknown emails — to prevent
    # timing-based email enumeration (I1: constant-time login).
    password_ok = verify_password(
        body.password,
        user.password_hash if (user and user.password_hash) else _DUMMY_HASH,
    )
    if not user or not user.password_hash or not password_ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Look up the org's display name (organizations is not RLS-scoped, so this
    # works on the pre-tenant-context login path). The token id stays authoritative.
    org_name: str | None = None
    if user.organization_id is not None:
        org_obj = await db.get(Organization, user.organization_id)
        org_name = org_obj.name if org_obj is not None else None

    token = create_access_token(
        sub=str(user.id),
        role=user.role,
        org=user.organization_id,
        org_name=org_name,
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
async def me(current: dict[str, Any] = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user's claims (sub + role + org)."""
    return MeResponse(
        sub=current["sub"],
        role=current["role"],
        org=current.get("org"),
        org_name=current.get("org_name"),
    )
