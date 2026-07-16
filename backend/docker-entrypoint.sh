#!/usr/bin/env sh
# Production entrypoint: apply migrations, then start the API server.
#
# Migrations run as the role in DATABASE_URL_SYNC (the DB owner/admin), which
# is correct — schema DDL must run as the owner, not the least-privilege
# app role. The runtime app, however, should connect as the non-superuser
# `curricmesh_app` role so Postgres RLS actually engages (see AGENT_LESSONS
# P-001 and scripts/create_app_role.sql).
set -e

# If a command was passed (Docker CMD / Render `dockerCommand` — e.g. the
# freshness cron's `python -m scripts.freshness_pipeline_run`), exec it
# directly and SKIP migrations: the web service owns schema DDL, and cron
# services run as the least-privilege app role which has no DDL rights.
if [ "$#" -gt 0 ]; then
    echo "[entrypoint] Executing command: $*"
    exec "$@"
fi

echo "[entrypoint] Running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
