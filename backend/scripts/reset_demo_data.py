"""Reset ALL application data — TRUNCATE every app table except alembic_version.

Use to clear a PARTIAL seed before re-running ``seed.bootcamp_curriculum``
(the seed is not idempotent). DESTRUCTIVE — demo / bootstrap environments only.

Guarded: refuses unless ``CONFIRM_RESET=yes`` is set, so it can never run by
accident. Keeps the schema + migration history (``alembic_version``) intact, so
no re-migration is needed afterward — just re-run the seed.

Usage (from backend/, with the target DATABASE_URL exported):
    CONFIRM_RESET=yes python -m scripts.reset_demo_data
"""
from __future__ import annotations

import asyncio
import os
import sys

import app.models  # noqa: F401 — importing the package registers every table on Base.metadata
from app.database import Base, engine


async def _reset() -> None:
    tables = [f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name != "alembic_version"]
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    await engine.dispose()
    print(f"Reset complete — truncated {len(tables)} tables (alembic_version kept).")


if __name__ == "__main__":
    if os.environ.get("CONFIRM_RESET") != "yes":
        print("Refusing: set CONFIRM_RESET=yes to wipe ALL application data (demo/bootstrap reset only).")
        sys.exit(1)
    asyncio.run(_reset())
