# CurricMesh — Agent Lessons (project-specific gotchas)

Project-specific traps discovered while working in this repo. Read at session
start. Append after any correction that exposes a repo gotcha.

---

## Postgres RLS

### P-001 · Superusers bypass RLS even under FORCE ROW LEVEL SECURITY
**Observed:** MT3, 2026-06-04.

The local/test DB role (`curricmesh`) is a **superuser**. Postgres superusers
bypass row-level security entirely — `ENABLE` + `FORCE ROW LEVEL SECURITY` and a
`tenant_isolation` policy will *not* filter their reads. So a conftest session
that sets `app.current_org` will still see every tenant's rows.

Consequences:
- Existing tests stay green under RLS only because write-stamping (the
  `organization_id` column default) + the matching GUC keep rows self-consistent
  — not because reads are actually filtered for the superuser.
- To *prove* RLS filters (or to test cross-tenant isolation), you MUST run the
  query as a NOSUPERUSER role: `SET LOCAL ROLE <role>` inside the transaction.
  Role DDL (`CREATE ROLE`/`GRANT`) is transactional in Postgres, so wrapping it
  in a transaction and `ROLLBACK`-ing afterward cleans up the throwaway role
  with no leakage between tests. See
  `tests/integration/test_tenant_isolation.py::test_rls_filters_cross_tenant_reads`.
- In production, the app DB role must be NOSUPERUSER for RLS to mean anything.

### P-002 · `SET` / `SET LOCAL` do NOT accept bind parameters
**Observed:** MT3, 2026-06-04.

`text("SET LOCAL app.current_org = :org")` with a bound `:org` raises
`syntax error at or near "$1"` — Postgres `SET` only takes literals. Use the
function form instead, which *does* take parameters and avoids string
interpolation (no injection surface on the org UUID):

```python
text("SELECT set_config('app.current_org', :org, true)")   # is_local=true  → txn-scoped
text("SELECT set_config('app.current_org', :org, false)")  # is_local=false → session-scoped
```

Session-scoped (`is_local=false`) `set_config` survives `commit()` and a *clean*
`rollback()` on the same connection — the long-lived conftest session relies on
this. BUT a `rollback()` after a flush that *errored* (e.g. the fail-closed
`require_org()` raise) can reset the connection's GUC; re-pin it afterward.

### P-005 · `use_org` alone is NOT enough for scripts/seeds under FORCE-RLS — bind the GUC too
**Observed:** seed RLS fix, 2026-07-08.

`app.tenant.use_org` sets only the Python ContextVar (app-layer write-stamp +
auto-filter). It does **not** touch Postgres' `app.current_org` GUC, so under
prod `FORCE ROW LEVEL SECURITY` on a NOSUPERUSER role every org-scoped write is
rejected (`new row violates row-level security policy`). Request handlers are
safe because `get_db` pushes the GUC via an `after_begin` listener — but any code
that builds its **own** session (seeds, back-fill, background jobs) must bind the
GUC itself. This is exactly why the demo seed shipped needing a workaround
(`scripts/seed_prod.py` temporarily un-forcing RLS): the tests all run as a
superuser (P-001), which bypasses RLS, so the gap was invisible in CI.

Rules:
- For a script/seed session, call `app.database.bind_session_org(session, org)`
  at the start of **every** `use_org` block — pair them (prefer one helper that
  does both, so they can't drift; see `seed/bootcamp_curriculum.py::_bound_org`).
  Or use `app.database.org_scoped_session(org_id)` when a fresh per-org session
  is acceptable.
- Use `is_local=false` (session-scoped), NOT `SET LOCAL`/`is_local=true`. A seed
  that seeds several orgs in **one transaction** (single `commit`) switches org
  mid-transaction; a transaction-local GUC goes stale on the switch. Session-
  scoped survives flushes + mid-block commits and is re-pointed per org.
- Reproduce FORCE-RLS locally with a NOSUPERUSER, NOBYPASSRLS role that is *not*
  the bootstrap superuser (the bootstrap role cannot be `ALTER ROLE … NOSUPERUSER`
  — create a separate role with DML grants and connect as it), else the proof is
  vacuous. Guard: `tests/integration/test_tenant_isolation.py::test_bind_session_org_admits_writes_and_survives_org_switch`.

---

## Write-stamping / tenant context

### P-003 · `require_org()` raises during INSERT default → wrapped in StatementError
**Observed:** MT3, 2026-06-04.

Domain models default `organization_id` to `lambda: require_org()`. When no
tenant context is set, the `RuntimeError` fires while SQLAlchemy builds the
INSERT, so it surfaces as `sqlalchemy.exc.StatementError` with `.orig` being the
`RuntimeError` — NOT a bare `RuntimeError`. Assert on `StatementError` and check
`exc.orig`.

### P-004 · users/organizations/sota_sources are deliberately NOT RLS-scoped
**Observed:** MT3, 2026-06-04.

Only the **13 curriculum-domain tables** are tenant-scoped. `users` and
`organizations` are queried in cross-tenant flows that run *before* any org
context exists (login-by-email, org provisioning) — scoping them breaks those
flows for zero isolation gain. `sota_sources` is a global shared corpus. The
authoritative list is `app.db.rls._ORG_SCOPED` / `TENANT_TABLES`, cross-checked
against ORM metadata by `tests/unit/test_rls_sql.py`. Don't "helpfully" add the
excluded three back.
