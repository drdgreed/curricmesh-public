-- ============================================================================
-- create_app_role.sql — least-privilege application role for CurricMesh.
-- ============================================================================
--
-- WHY THIS EXISTS (read AGENT_LESSONS.md P-001):
--   The 13 curriculum-domain tables have RLS policies under FORCE ROW LEVEL
--   SECURITY. But Postgres exempts SUPERUSER and BYPASSRLS roles from RLS
--   *even under FORCE*. So if production connects as the DB owner/superuser,
--   the DB-layer tenant isolation NEVER engages — the app-layer filter still
--   isolates tenants, but the RLS backstop is silently inert.
--
--   To make the RLS backstop real, the app must connect as a NON-superuser,
--   NON-BYPASSRLS role. That role is `curricmesh_app`, created here.
--
-- PRODUCTION REQUIREMENT:
--   The app's runtime DATABASE_URL MUST use `curricmesh_app` — NOT the
--   provider's default owner/superuser. Example:
--     postgresql+asyncpg://curricmesh_app:<password>@<host>:5432/<db>
--
--   Migrations (alembic) and seeding still run as the OWNER role (they are
--   DDL / cross-tenant operations). Only the live API uses `curricmesh_app`.
--
-- WHEN TO RUN:
--   AFTER `alembic upgrade head` has created the tables — the GRANTs below
--   target existing tables. The ALTER DEFAULT PRIVILEGES lines also cover any
--   tables created by future migrations (provided those migrations run as the
--   same owner role that runs this script).
--
-- HOW TO RUN (as the owner/admin role):
--   psql "$ADMIN_DATABASE_URL" -v app_password="'CHANGE_ME_STRONG_PASSWORD'" \
--        -f scripts/create_app_role.sql
--   (or replace :'app_password' inline before running)
-- ============================================================================

-- 1. Create the role if it does not already exist. NON-superuser, NO BYPASSRLS,
--    LOGIN-capable. Replace the password placeholder with a real secret.
DO
$$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'curricmesh_app') THEN
        -- NOSUPERUSER + NOBYPASSRLS are the bits that make RLS engage.
        CREATE ROLE curricmesh_app
            LOGIN
            NOSUPERUSER
            NOBYPASSRLS
            NOCREATEDB
            NOCREATEROLE
            PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
    END IF;
END
$$;

-- NOTE: do NOT `ALTER ROLE ... NOSUPERUSER NOBYPASSRLS` here. On managed Postgres
-- (Render/Railway/RDS) the owner role is NOT a superuser, and Postgres only lets
-- superusers change the SUPERUSER attribute — even to turn it OFF — so that ALTER
-- raises "permission denied to alter role" and (in one transaction) rolls back the
-- CREATE above. The CREATE ROLE already sets NOSUPERUSER + NOBYPASSRLS at creation,
-- which is what makes RLS engage, so the defensive ALTER is redundant anyway.
-- Set/rotate the password (uncomment and edit, or manage out-of-band):
-- ALTER ROLE curricmesh_app PASSWORD 'CHANGE_ME_STRONG_PASSWORD';

-- 2. Schema access. The app only needs to USE the schema, not own it.
GRANT USAGE ON SCHEMA public TO curricmesh_app;

-- 3. DML on all existing application tables (no DDL, no TRUNCATE, no ownership).
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO curricmesh_app;

-- 4. Sequences (needed for server-side default IDs / nextval()).
GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA public
    TO curricmesh_app;

-- 5. Future tables/sequences created by later migrations are covered
--    automatically — provided those migrations run as the same owner role
--    that runs this script (ALTER DEFAULT PRIVILEGES is owner-scoped).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO curricmesh_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO curricmesh_app;

-- ============================================================================
-- VERIFY (optional): these should both return `f` (false).
--   SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'curricmesh_app';
-- ============================================================================
