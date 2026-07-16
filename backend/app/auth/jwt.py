"""JWT creation and validation using PyJWT + HS256."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings

_ALGORITHM = "HS256"


def create_access_token(
    sub: str,
    role: str,
    org: str | uuid.UUID | None = None,
    org_name: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Return a signed JWT with *sub*, *role*, *exp*, and optionally *org*.

    The ``org`` claim (tenant id) is included only when *org* is not None, so
    existing callers that don't pass a tenant keep producing org-less tokens.
    ``org_name`` is a display-only convenience claim; the authoritative tenant
    identifier is still ``org``.
    """
    minutes = (
        expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=minutes),
    }
    if org is not None:
        payload["org"] = str(org)
    if org_name is not None:
        payload["org_name"] = org_name
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate *token*; raise ValueError on invalid/expired."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
