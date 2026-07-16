"""Tenant-isolation guarantees for the MT3 multi-tenancy layer.

Two distinct guarantees, proven separately:

1. **Write-stamping fail-closed.** With no tenant context set, flushing a domain
   row raises — the ``organization_id`` column default calls ``require_org()``,
   which refuses to stamp an unscoped row. SQLAlchemy surfaces the underlying
   ``RuntimeError`` wrapped in a ``StatementError`` (it fires while building the
   INSERT). This protects the write path regardless of DB role: it fires in
   Python, before any SQL leaves the process.

2. **Read enforcement (RLS is live).** A row written under DEFAULT_ORG is
   invisible when the connection's ``app.current_org`` GUC names a *different*
   org. Postgres superusers bypass RLS even under FORCE, and the test DB role is
   a superuser — so we drop to a throwaway NOSUPERUSER role via ``SET LOCAL
   ROLE`` to observe the policy actually filtering. Role DDL is transactional in
   Postgres, so a final ``rollback()`` removes the throwaway role cleanly.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError, StatementError

from app.models.curriculum import Curriculum
from app.tenant import current_org, set_current_org, use_org
from tests.conftest import DEFAULT_ORG_ID


def _pin_default_org(session):
    """Re-assert the fixture's DEFAULT_ORG session GUC (returns a coroutine)."""
    return session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(DEFAULT_ORG_ID)},
    )


async def test_insert_without_context_fails_closed(db_session):
    """No org context → flushing a domain row raises (require_org fail-closed).

    We clear the ContextVar only for this assertion, then restore it and roll
    back the poisoned transaction so the shared fixture is left clean.
    """
    token = set_current_org(None)
    try:
        assert current_org.get() is None
        db_session.add(Curriculum(name="Orphan", slug=f"orphan-{uuid.uuid4().hex[:6]}"))
        with pytest.raises(StatementError) as exc_info:
            await db_session.flush()
        # The wrapped cause is our fail-closed RuntimeError.
        assert isinstance(exc_info.value.orig, RuntimeError)
        assert "No tenant context" in str(exc_info.value.orig)
    finally:
        current_org.reset(token)
        await db_session.rollback()
        # The errored flush reset the connection's GUC; re-pin it.
        await _pin_default_org(db_session)


async def test_rls_filters_cross_tenant_reads(db_session):
    """A DEFAULT_ORG row is hidden when the GUC names another org — under RLS.

    Runs the cross-tenant query as a NOSUPERUSER role so the FORCEd policy is
    actually applied (superusers bypass RLS). Role creation and the final
    rollback are both transactional, so no role leaks between tests.
    """
    # 1. Write a row stamped DEFAULT_ORG (ContextVar default), commit it.
    slug = f"rls-{uuid.uuid4().hex[:8]}"
    db_session.add(Curriculum(name="Scoped", slug=slug))
    await db_session.commit()

    role = f"rls_probe_{uuid.uuid4().hex[:8]}"
    try:
        # 2. A throwaway non-superuser with just enough to read curricula.
        await db_session.execute(text(f'CREATE ROLE "{role}" NOSUPERUSER'))
        await db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await db_session.execute(text(f'GRANT SELECT ON curricula TO "{role}"'))
        await db_session.execute(text(f'SET LOCAL ROLE "{role}"'))

        # 3a. Same-org read sees the row.
        await db_session.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": str(DEFAULT_ORG_ID)},
        )
        same = await db_session.execute(
            text("SELECT count(*) FROM curricula WHERE slug = :s"), {"s": slug}
        )
        assert same.scalar() == 1, "same-org read should see the row"

        # 3b. A different org sees nothing — RLS is filtering.
        await db_session.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": str(uuid.uuid4())},
        )
        other = await db_session.execute(
            text("SELECT count(*) FROM curricula WHERE slug = :s"), {"s": slug}
        )
        assert other.scalar() == 0, "cross-tenant read must be empty (RLS active)"
    finally:
        # Rolls back SET LOCAL ROLE, the local GUC, AND the role DDL.
        await db_session.rollback()
        await _pin_default_org(db_session)


async def test_rls_with_check_blocks_cross_tenant_write(db_session):
    """A NOSUPERUSER role with the GUC at org A cannot INSERT a row carrying org B.

    The mirror of ``test_rls_filters_cross_tenant_reads`` for the *write* path.
    Reads are filtered by the policy's ``USING`` clause; writes are gated by its
    ``WITH CHECK`` clause. We drop to a throwaway NOSUPERUSER role (superusers
    bypass RLS even under FORCE — P-001) with the connection's ``app.current_org``
    GUC pinned to org A, then:

    * inserting a ``curricula`` row whose ``organization_id`` = org A SUCCEEDS
      (matches WITH CHECK), and
    * inserting one whose ``organization_id`` = org B is REJECTED by the policy
      (SQLSTATE 42501 / InsufficientPrivilege → SQLAlchemy ``ProgrammingError``).

    We grant the role INSERT (and SELECT, for the positive read-back) so the only
    thing that can reject the cross-org INSERT is the WITH CHECK policy itself —
    not a missing table privilege. We must set ``organization_id`` explicitly via
    raw SQL: the app's column default would otherwise stamp it from context,
    making a cross-org write impossible to express. Role DDL + SET LOCAL ROLE are
    transactional, so the final rollback removes the role and restores the GUC.
    """
    org_a = DEFAULT_ORG_ID
    org_b = uuid.uuid4()

    role = f"wc_probe_{uuid.uuid4().hex[:8]}"
    try:
        await db_session.execute(text(f'CREATE ROLE "{role}" NOSUPERUSER'))
        await db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await db_session.execute(
            text(f'GRANT SELECT, INSERT ON curricula TO "{role}"')
        )

        # Confirm the role really is NON-superuser / NON-bypassrls *before*
        # trusting any rejection — else the proof is vacuous (P-001).
        attrs = (
            await db_session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = :r"
                ),
                {"r": role},
            )
        ).one()
        assert attrs.rolsuper is False, "probe role must be NOSUPERUSER"
        assert attrs.rolbypassrls is False, "probe role must NOT have BYPASSRLS"

        await db_session.execute(text(f'SET LOCAL ROLE "{role}"'))
        await db_session.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": str(org_a)},
        )

        # Positive case: a row whose org matches the GUC passes WITH CHECK.
        ok_slug = f"wc-ok-{uuid.uuid4().hex[:8]}"
        await db_session.execute(
            text(
                "INSERT INTO curricula (id, name, slug, organization_id) "
                "VALUES (:id, :n, :s, :org)"
            ),
            {"id": str(uuid.uuid4()), "n": "Same Org", "s": ok_slug, "org": str(org_a)},
        )
        seen = await db_session.execute(
            text("SELECT count(*) FROM curricula WHERE slug = :s"), {"s": ok_slug}
        )
        assert seen.scalar() == 1, "same-org insert must succeed and be visible"

        # Negative case: a row carrying org B is rejected by WITH CHECK.
        with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
            await db_session.execute(
                text(
                    "INSERT INTO curricula (id, name, slug, organization_id) "
                    "VALUES (:id, :n, :s, :org)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "n": "Cross Org",
                    "s": f"wc-bad-{uuid.uuid4().hex[:8]}",
                    "org": str(org_b),
                },
            )
        # SQLSTATE 42501 = insufficient_privilege, Postgres' code for a
        # row-security WITH CHECK violation. Assert it's the policy, not some
        # unrelated error.
        assert exc_info.value.orig.sqlstate == "42501", (
            f"expected RLS WITH CHECK rejection (42501), got "
            f"{exc_info.value.orig.sqlstate!r}: {exc_info.value.orig}"
        )
    finally:
        # Rolls back SET LOCAL ROLE, the local GUC, the failed INSERT, AND the
        # role DDL — leaving the shared fixture clean.
        await db_session.rollback()
        await _pin_default_org(db_session)


async def test_bind_session_org_admits_writes_and_survives_org_switch(db_session):
    """``bind_session_org`` (the seed's FORCE-RLS fix) pushes the org to the DB
    GUC so an org-scoped write passes ``WITH CHECK`` — and, being session-scoped,
    supports switching orgs WITHIN a single transaction (the multi-org seed path).

    Regression guard for the shipped bug: the seed set only the ``use_org``
    ContextVar, never the DB GUC, so under production FORCE-RLS on a non-superuser
    role every org-scoped INSERT was rejected. The whole suite otherwise runs as a
    superuser (RLS-bypassed), which is exactly why the bug shipped — so here we
    drop to a throwaway NOSUPERUSER role via ``SET LOCAL ROLE`` to make RLS bite.

    Proves three things in one NOSUPERUSER transaction:

    * bind org A → an A-row INSERT SUCCEEDS (GUC matches ``WITH CHECK``);
    * bind org B → a B-row INSERT SUCCEEDS in the SAME transaction (the switch the
      single-commit seed relies on; a ``SET LOCAL``/transaction-scoped GUC could
      not do this);
    * without re-binding (GUC still at B), an A-row INSERT is REJECTED (42501) —
      confirming the bind is load-bearing, not incidental.
    """
    from app.database import bind_session_org

    org_a = DEFAULT_ORG_ID
    org_b = uuid.uuid4()
    # org_b must exist (organization_id is FK → organizations). organizations is
    # not RLS-scoped, so this insert is unconstrained; do it as the superuser
    # connection before dropping to the probe role.
    await db_session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(org_b), "n": "Bind Probe Org B"},
    )

    role = f"bind_probe_{uuid.uuid4().hex[:8]}"
    try:
        await db_session.execute(text(f'CREATE ROLE "{role}" NOSUPERUSER'))
        await db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await db_session.execute(
            text(f'GRANT SELECT, INSERT ON curricula TO "{role}"')
        )
        attrs = (
            await db_session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"
                ),
                {"r": role},
            )
        ).one()
        assert attrs.rolsuper is False, "probe role must be NOSUPERUSER"
        assert attrs.rolbypassrls is False, "probe role must NOT have BYPASSRLS"

        await db_session.execute(text(f'SET LOCAL ROLE "{role}"'))

        async def _insert(org: uuid.UUID) -> str:
            slug = f"bind-{uuid.uuid4().hex[:8]}"
            await db_session.execute(
                text(
                    "INSERT INTO curricula (id, name, slug, organization_id) "
                    "VALUES (:id, :n, :s, :org)"
                ),
                {"id": str(uuid.uuid4()), "n": "Bind", "s": slug, "org": str(org)},
            )
            return slug

        # Bind A → A-write succeeds.
        await bind_session_org(db_session, org_a)
        slug_a = await _insert(org_a)
        assert (
            await db_session.execute(
                text("SELECT count(*) FROM curricula WHERE slug = :s"), {"s": slug_a}
            )
        ).scalar() == 1

        # Switch to B in the SAME transaction → B-write succeeds.
        await bind_session_org(db_session, org_b)
        slug_b = await _insert(org_b)
        assert (
            await db_session.execute(
                text("SELECT count(*) FROM curricula WHERE slug = :s"), {"s": slug_b}
            )
        ).scalar() == 1

        # Load-bearing check: GUC is now at B; an A-row is rejected by WITH CHECK.
        with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
            await _insert(org_a)
        assert exc_info.value.orig.sqlstate == "42501", (
            f"expected RLS WITH CHECK rejection (42501), got "
            f"{exc_info.value.orig.sqlstate!r}: {exc_info.value.orig}"
        )
    finally:
        # Rollback clears SET LOCAL ROLE, the failed INSERT, and the role DDL; the
        # session-scoped GUC set by bind_session_org survives it, so re-pin.
        await db_session.rollback()
        await _pin_default_org(db_session)


async def test_use_org_stamps_explicit_tenant(db_session):
    """A row created under ``use_org(other)`` carries that org, not DEFAULT_ORG.

    Confirms write-stamping reads the *ambient* context at insert time — the
    mechanism the AI Researcher relies on (its CCRs inherit the request's org,
    not the actor's).
    """
    other_org = uuid.UUID("00000000-0000-0000-0000-0000000ab1e2")
    # organizations is not RLS-scoped, so this insert is unconstrained.
    await db_session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other_org), "n": "Other Org"},
    )
    with use_org(other_org):
        cur = Curriculum(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        db_session.add(cur)
        await db_session.flush()
        assert cur.organization_id == other_org
    await db_session.rollback()
    await _pin_default_org(db_session)
