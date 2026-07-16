"""One-shot migrate/bootstrap orchestrator for the self-host docker stack.

Runs, in order, AS THE DB OWNER (a superuser on the bundled postgres:16, so it
bypasses RLS and the seed writes tenant rows without the prod RLS-disable dance):

  1. alembic upgrade head                  — create/upgrade the schema (DDL)
  2. scripts/create_app_role.sql           — create the least-privilege
     `curricmesh_app` role + GRANTs + ALTER DEFAULT PRIVILEGES, then set its
     password from $APP_DB_PASSWORD (the SQL ships a placeholder password).
  3. python -m seed.bootcamp_curriculum    — seed the demo orgs + immutable model

Idempotent / safe to re-run:
  - alembic upgrade head is a no-op once at head.
  - create_app_role.sql uses IF NOT EXISTS for the role; GRANTs are idempotent;
    the password ALTER simply re-asserts the password each run.
  - the seed is idempotent (it SKIPs orgs whose curriculum already exists).

This file is bind-mounted into the migrate container next to create_app_role.sql,
and runs inside the backend image (which has alembic, psycopg2, and the app/seed
packages installed).
"""

import os
import subprocess
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_PATH = os.path.join(HERE, "create_app_role.sql")


def _owner_sync_dsn() -> str:
    """The OWNER (superuser) connection, as a libpq/psycopg2 DSN.

    DATABASE_URL_SYNC is set to the SQLAlchemy form
    (postgresql+psycopg2://... or postgresql://...). psycopg2 wants a plain
    postgresql:// scheme, so normalise the +driver suffix away.
    """
    dsn = os.environ["DATABASE_URL_SYNC"]
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


def run_migrations() -> None:
    print("[migrate] 1/3 alembic upgrade head", flush=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def create_app_role() -> None:
    print("[migrate] 2/3 create_app_role.sql + set app role password", flush=True)
    app_password = os.environ.get("APP_DB_PASSWORD")
    if not app_password:
        sys.exit("[migrate] FATAL: APP_DB_PASSWORD is not set")

    with open(SQL_PATH, encoding="utf-8") as fh:
        sql = fh.read()

    conn = psycopg2.connect(_owner_sync_dsn(), connect_timeout=30)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Creates the role (with a placeholder password) + GRANTs.
            cur.execute(sql)
            # Set the REAL password from the environment. The role name is fixed
            # by the SQL; psycopg2 cannot parameterise an identifier or a role
            # password literal, so quote it safely as a string literal.
            cur.execute(
                "ALTER ROLE curricmesh_app PASSWORD %s",
                (app_password,),
            )
            cur.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'curricmesh_app'"
            )
            rolsuper, rolbypassrls = cur.fetchone()
            print(
                f"[migrate]   curricmesh_app rolsuper={rolsuper} "
                f"rolbypassrls={rolbypassrls} (both must be False for RLS)",
                flush=True,
            )
            if rolsuper or rolbypassrls:
                sys.exit("[migrate] FATAL: app role would bypass RLS")
    finally:
        conn.close()


def run_seed() -> None:
    print("[migrate] 3/3 python -m seed.bootcamp_curriculum", flush=True)
    subprocess.run([sys.executable, "-m", "seed.bootcamp_curriculum"], check=True)


def main() -> None:
    run_migrations()
    create_app_role()
    run_seed()
    print("[migrate] DONE — schema migrated, app role ready, demo data seeded.", flush=True)


if __name__ == "__main__":
    main()
