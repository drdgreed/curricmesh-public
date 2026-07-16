"""RBAC dependency factories and the get_current_user dependency."""

import uuid
from collections.abc import AsyncIterator
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException

from app.auth.jwt import decode_token
from app.tenant import current_org, set_current_org


async def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Extract Bearer token from Authorization header, decode it, return claims.

    Raises HTTPException(401) on missing or invalid token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ")
    try:
        return decode_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def tenant_context(
    current: dict[str, Any] = Depends(get_current_user),
) -> "AsyncIterator[dict[str, Any]]":
    """Bind the token's ``org`` claim to the ``current_org`` ContextVar for the
    request, then reset it on the way out.

    An **async** ``yield`` dependency: it must run on the request's event-loop
    task (not a threadpool) so the ``ContextVar`` set and ``reset`` happen in the
    SAME context — a sync generator dependency would be run via FastAPI's
    ``contextmanager_in_threadpool``, where ``current_org.reset(token)`` raises
    "Token was created in a different Context". The explicit set-on-entry /
    reset-in-``finally`` scope keeps it safe in background tasks and consistent
    with ``app.tenant.use_org``. Routers depend on this so the ContextVar is set
    BEFORE the endpoint's ``get_db`` reads it to push down the RLS GUC. A missing
    or malformed ``org`` claim is rejected with 401 rather than running unscoped.
    """
    org = current.get("org")
    if not org:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    try:
        token = set_current_org(uuid.UUID(org))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Missing tenant context") from exc
    try:
        yield current
    finally:
        current_org.reset(token)


def require_roles(*roles: str) -> Callable:
    """Return a dependency that enforces the caller's role is in *roles*.

    The returned callable accepts *current* either via FastAPI's
    ``Depends(get_current_user)`` injection (router usage) or as an
    explicit keyword argument (unit-test usage).
    """

    def checker(
        current: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if current.get("role") not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{current.get('role')}' is not authorised for this endpoint",
            )
        return current

    return checker
