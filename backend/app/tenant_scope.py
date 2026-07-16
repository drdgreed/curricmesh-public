"""App-layer tenant scoping — the second enforcement layer.

The DB-layer backstop is Postgres RLS (``app/db/rls.py``), but RLS is bypassed
by any superuser / ``BYPASSRLS`` role — and the dev/CI ``curricmesh`` role *is*
a superuser. So under that role, RLS alone does NOT isolate tenants. This module
closes that gap at the **application** layer.

A single ``do_orm_execute`` event listener on the ORM ``Session`` auto-adds
``organization_id == current_org`` to every ORM SELECT that touches a
``TenantScoped`` entity — whenever a tenant context is set. Because it runs in
the app (not the DB), it isolates reads regardless of the DB role, so the test
suite can *prove* isolation while connected as the superuser.

When no tenant context is set the listener is a no-op (no filter added). That is
deliberate: the RLS GUC fail-closed policy + the write-time ``require_org``
column default are the guards on the unscoped path; we don't want to break the
inherently cross-tenant flows (login, org provisioning, seeds) that run before a
context exists.

The listener is registered once, as an import side-effect of this module, so it
is always active for any ``Session`` (and thus any ``AsyncSession``, which wraps
a sync ``Session``). ``app.database`` imports it to guarantee registration.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.tenant import get_current_org


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(execute_state) -> None:
    """Auto-scope ORM SELECTs to the current tenant.

    Guards:
    * ``is_select`` — only filter reads (INSERT/UPDATE/DELETE are guarded by the
      write-time column default + RLS WITH CHECK).
    * ``not is_column_load`` / ``not is_relationship_load`` — don't interfere
      with SQLAlchemy's internal refresh / lazy-load emits; those are already
      keyed to a specific parent row.

    ``with_loader_criteria(TenantScoped, ...)`` targets the mixin, so the
    criterion applies to *every* subclass (all 13 domain tables), including
    aliases (joins, subqueries).
    """
    if (
        execute_state.is_select
        and not execute_state.is_column_load
        and not execute_state.is_relationship_load
    ):
        org = get_current_org()
        if org is not None:
            # Imported lazily (not at module top) so this module's import graph is
            # just app.tenant + sqlalchemy. Importing app.models._tenant eagerly
            # would run the app.models package __init__ → app.database → back here,
            # a cycle when app.tenant_scope is imported first.
            from app.models._tenant import TenantScoped

            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    TenantScoped,
                    lambda cls: cls.organization_id == org,
                    include_aliases=True,
                )
            )


def register() -> None:
    """No-op hook: importing this module already registers the listener.

    Provided so callers can express the dependency explicitly
    (``app.tenant_scope.register()``) without relying solely on import order.
    """
