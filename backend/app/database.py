import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.tenant import get_current_org, use_org

engine = create_async_engine(settings.DATABASE_URL, echo=False)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


# Register the app-layer tenant auto-filter (do_orm_execute listener). Importing
# the module is the registration side-effect; the explicit call documents the
# dependency and guarantees it happens once the engine/sessionmaker exist. This
# is the second isolation layer (RLS is the DB backstop); it works even when the
# DB role bypasses RLS (the dev/CI superuser). Imported here, after Base, to
# avoid an import cycle (tenant_scope → app.models._tenant → app.tenant).
from app import tenant_scope  # noqa: E402

tenant_scope.register()


# Postgres ``SET``/``SET LOCAL`` does not accept bind parameters, so we drive the
# per-transaction GUC through ``set_config(name, value, is_local)`` — the
# function form *does* take parameters, keeping us free of string interpolation
# (no SQL injection surface on the org UUID).
_SET_LOCAL_ORG = text("SELECT set_config('app.current_org', :org, true)")

# Session-scoped variant (``is_local=false``): the GUC survives flushes AND
# ``commit()`` on the same connection, and is overwritten only when the next org
# binds. This is exactly what a multi-org SEED needs — it switches tenants
# (``use_org``) repeatedly, sometimes several orgs within a SINGLE transaction,
# so a transaction-local (``SET LOCAL``) GUC would go stale the moment the org
# changes mid-transaction. Request handlers must NOT use this (they use the
# transaction-local ``get_db`` path); it is for scripts/seeds that own their own
# tenant scoping. Value is bound (never interpolated) — no injection surface.
_SET_SESSION_ORG = text("SELECT set_config('app.current_org', :org, false)")


async def bind_session_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Push *org_id* to the DB GUC ``app.current_org`` at SESSION scope.

    The DB-layer counterpart to ``app.tenant.use_org`` (which sets only the
    Python ContextVar). Under production ``FORCE ROW LEVEL SECURITY`` on a
    non-superuser role, every org-scoped read/write is filtered by this GUC; the
    ContextVar alone is insufficient (it drives app-layer write-stamping only).

    Session-scoped so it holds across flushes and mid-block commits and updates
    when the next org binds — the semantics a multi-org seed needs when it
    switches tenants within one transaction. Call it at the start of every
    tenant block (paired with ``use_org``); the two must always move together.
    """
    org_literal = str(uuid.UUID(str(org_id)))  # validate → UUID; bound, not inlined
    await session.execute(_SET_SESSION_ORG, {"org": org_literal})


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped session bound to the caller's tenant.

    Reads the ``current_org`` ContextVar — which an authenticated router's
    dependency chain sets via ``app.auth.rbac.tenant_context`` *before*
    ``get_db`` runs — and pushes it down to Postgres as the transaction-local
    GUC ``app.current_org``. The RLS policies (``app/db/rls.py``) filter every
    domain table on that GUC, so reads and writes are tenant-scoped.

    ``set_config(..., is_local=true)`` is transaction-scoped. A request handler
    may ``commit()`` mid-request and then read again (e.g. ``refresh()``); the
    commit ends the transaction and a fresh one begins for the follow-up query.
    To keep the GUC in effect across that boundary we (re-)issue it on EVERY
    transaction begin via an ``after_begin`` listener scoped to this session —
    not just once up front. Without this, a non-superuser DB role (prod) loses
    the GUC after commit and FORCE-RLS hides the just-written row → 500.

    If no org context is set (e.g. an unauthenticated route that still depends
    on ``get_db``), the GUC stays unset and the fail-closed RLS policies match
    no domain rows — never a cross-tenant leak.
    """
    async with AsyncSessionLocal() as session:
        org = get_current_org()
        if org is not None:
            org_literal = str(uuid.UUID(str(org)))  # validated UUID — safe to inline

            def _reaffirm_org(_sess, _trans, connection) -> None:
                # Runs on every transaction begin (initial + post-commit). The
                # value is a validated UUID literal, so there is no injection
                # surface even though set_config is inlined here.
                connection.exec_driver_sql(
                    f"SELECT set_config('app.current_org', '{org_literal}', true)"
                )

            event.listen(session.sync_session, "after_begin", _reaffirm_org)
            # Set it now for the transaction the first query will open.
            await session.execute(_SET_LOCAL_ORG, {"org": org_literal})
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def async_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a privileged session that does NOT auto-set the tenant GUC.

    This is the deliberate escape hatch for code that operates across tenants or
    establishes the context itself: seeds, migrations, org provisioning, and
    background jobs that wrap their work in ``app.tenant.use_org`` per org. Such
    callers are responsible for their own scoping; nothing here pins the session
    to one tenant.

    Privileged path — keep its blast radius small. Request handlers must use
    ``get_db`` (tenant-scoped), never this.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def org_scoped_session(org_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """Session for scripts/background jobs bound to one org.

    Sets the ``current_org`` ContextVar (app-layer auto-filter) AND pushes the
    ``app.current_org`` GUC on every transaction begin (DB-layer RLS) — the
    same mechanism as ``get_db()``, which scripts can't use because it's a
    FastAPI dependency.  Use this instead of bare ``AsyncSessionLocal +
    use_org`` for any tenant-scoped script work.

    Under production FORCE ROW LEVEL SECURITY on a non-superuser DB role,
    every tenant-scoped read/write requires the GUC; the ContextVar alone is
    insufficient (see AGENT_LESSONS P-001/P-011).  This helper keeps both
    layers in sync so script sessions are production-safe.
    """
    org_literal = str(uuid.UUID(str(org_id)))  # validated UUID — safe to inline

    def _reaffirm_org(_sess, _trans, connection) -> None:
        # Runs on every transaction begin (initial + post-commit).  Keeps the
        # transaction-local GUC alive across commits the same way get_db() does.
        connection.exec_driver_sql(
            f"SELECT set_config('app.current_org', '{org_literal}', true)"
        )

    with use_org(org_id):
        async with AsyncSessionLocal() as session:
            event.listen(session.sync_session, "after_begin", _reaffirm_org)
            # Set now for the transaction the first query will open.
            await session.execute(_SET_LOCAL_ORG, {"org": org_literal})
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                event.remove(session.sync_session, "after_begin", _reaffirm_org)


# Convenience helper for local/test setup only — Alembic is the migration path of record; do NOT wire this to app startup.
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
