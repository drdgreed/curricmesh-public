"""Tenant context primitive for Postgres-RLS multi-tenancy.

A single ContextVar, ``current_org``, carries the active tenant for the
duration of a request (or a seed/test/background block). Later tasks wire
this into write-stamping (model column defaults) and the RLS read GUC.

This module is intentionally dependency-free (stdlib only): ``app.models``
will import it, so it must never import from ``app.models`` (or anything
under ``app``) to avoid an import cycle.
"""

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator

current_org: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "current_org", default=None
)


def get_current_org() -> uuid.UUID | None:
    """Return the org bound to the current context, or None if unset."""
    return current_org.get()


def set_current_org(org: uuid.UUID | None) -> contextvars.Token:
    """Bind *org* to the current context; return the Token for resetting."""
    return current_org.set(org)


@contextmanager
def use_org(org_id: uuid.UUID) -> Iterator[None]:
    """Set the tenant context for the block, resetting it afterward.

    Use for seeds, tests, and background work where the prior context must
    be restored on exit (including the nested case).
    """
    token = current_org.set(org_id)
    try:
        yield
    finally:
        current_org.reset(token)


def require_org() -> uuid.UUID:
    """Return the current org, or raise if none is set (fail closed).

    Used later as a NOT NULL column default so an unscoped write fails loudly
    rather than silently leaking a row across tenants.
    """
    org = current_org.get()
    if org is None:
        raise RuntimeError(
            "No tenant context set (current_org); refusing to write an unscoped row"
        )
    return org
