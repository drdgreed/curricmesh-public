"""Seed a FORCE-RLS database (e.g. prod on Render) in one guarded command.

WHY THIS EXISTS: the seed writes org-scoped rows via the ``use_org`` ContextVar
but never pushes it to Postgres' ``app.current_org`` GUC (only ``get_db`` does).
Under ``FORCE ROW LEVEL SECURITY`` every insert is therefore rejected — the seed
only works where RLS isn't enforced (a ``create_all`` dev DB). The connecting
role OWNS these tables (it ran the migrations), so temporarily switching them to
``NO FORCE`` lets the owner bypass RLS for the duration of the seed; ``FORCE`` is
then restored — ALWAYS, even if the seed fails.

This also TRUNCATEs first, because the seed is not idempotent.

DESTRUCTIVE — demo / bootstrap environments only. Guarded by
``CONFIRM_SEED_PROD=yes``. Keeps the schema + migration history intact.

Usage (from backend/, with the target DATABASE_URL exported):
    CONFIRM_SEED_PROD=yes python -m scripts.seed_prod
"""
from __future__ import annotations

import asyncio
import os
import sys

import app.models  # noqa: F401 — importing registers every table on Base.metadata
from app.database import Base, engine
from app.db.rls import _ORG_SCOPED
from seed.bootcamp_curriculum import _main as run_seed


async def _set_force(force: bool) -> None:
    verb = "FORCE" if force else "NO FORCE"
    async with engine.begin() as conn:
        for table in _ORG_SCOPED:
            await conn.exec_driver_sql(f'ALTER TABLE "{table}" {verb} ROW LEVEL SECURITY')


async def _truncate_all() -> None:
    tables = [f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name != "alembic_version"]
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")


async def main() -> None:
    print(f"[1/3] wiping all data + un-forcing RLS on {len(_ORG_SCOPED)} org-scoped tables…")
    await _truncate_all()
    await _set_force(False)
    try:
        print("[2/3] running the seed (RLS bypassed as table owner)…")
        await run_seed()
    finally:
        print("[3/3] restoring FORCE ROW LEVEL SECURITY…")
        await _set_force(True)
        await engine.dispose()
    print("Done — prod seeded and FORCE RLS restored on every org-scoped table.")


if __name__ == "__main__":
    if os.environ.get("CONFIRM_SEED_PROD") != "yes":
        print("Refusing: set CONFIRM_SEED_PROD=yes — this WIPES and re-seeds the target database.")
        sys.exit(1)
    asyncio.run(main())
