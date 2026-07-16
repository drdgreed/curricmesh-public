"""Alembic environment — sync psycopg2 path for straightforward autogenerate."""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Ensure the backend package root is on sys.path so `app` is importable.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Load .env so DATABASE_URL_SYNC is available when running `alembic` CLI.
# ---------------------------------------------------------------------------
env_file = Path(__file__).resolve().parents[1] / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# ---------------------------------------------------------------------------
# Import all models so Base.metadata is fully populated.
# ---------------------------------------------------------------------------
import app.models  # noqa: F401
from app.database import Base

# ---------------------------------------------------------------------------
# Alembic config + logging
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override the URL from the environment if present (wins over alembic.ini).
# DATABASE_URL_SYNC must be the OWNER role — Alembic runs DDL.
db_url = os.environ.get("DATABASE_URL_SYNC") or config.get_main_option("sqlalchemy.url")
# Normalize any scheme to the psycopg2 (sync) driver Alembic needs. Managed
# providers hand back postgres:// / postgresql:// — both must become +psycopg2
# here (AGENT_LESSONS P-007). Idempotent for an already-+psycopg2 URL.
if not db_url.startswith("postgresql+psycopg2://"):
    for _bad in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if db_url.startswith(_bad):
            db_url = "postgresql+psycopg2://" + db_url[len(_bad):]
            break
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Render CREATE TYPE for native PG enums
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
