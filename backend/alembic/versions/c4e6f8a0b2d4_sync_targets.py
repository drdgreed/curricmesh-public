"""sync_targets + SyncLog Phase-4 columns

Adds the ``sync_targets`` tenant-scoped table (per-tenant outbound-sync
destination config for Phase 4), plus two column changes on ``sync_logs``:

1. CREATE TABLE ``sync_targets`` with RLS via _enable_sql / _disable_sql
   (mirrors b3c5d7e9f1a2_gap_assessments.py exactly — P-009: hardcoded tuple).
2. ALTER COLUMN ``sync_logs.version_id`` DROP NOT NULL — new-model releases
   have no legacy Version row; existing callers still pass it.
3. ADD COLUMN ``sync_logs.curriculum_version_id`` UUID FK → curriculum_versions
   (ON DELETE SET NULL, nullable) — set by the Phase-4 sync service.

``sync_logs`` is already RLS-registered; its entry is NOT touched here.

Downgrade reverses all three — BUT re-adding NOT NULL to sync_logs.version_id FAILS if any Phase-4 row (version_id IS NULL) exists; downgrade is only safe before the first Phase-4 sync:
  - drop sync_logs columns (curriculum_version_id first, then restore NOT NULL)
  - drop RLS → indexes → sync_targets table

Revision ID: c4e6f8a0b2d4
Revises: b3c5d7e9f1a2
Create Date: 2026-07-05 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'c4e6f8a0b2d4'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("sync_targets",)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. Create sync_targets (tenant-scoped, Phase-4 config-as-data).
    # ------------------------------------------------------------------
    op.create_table(
        "sync_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="github_pr"),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_id"], ["curricula.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sync_targets_organization_id",
        "sync_targets",
        ["organization_id"],
    )
    op.create_index(
        "ix_sync_targets_curriculum_id",
        "sync_targets",
        ["curriculum_id"],
    )

    # ------------------------------------------------------------------
    # RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    # Mirror b3c5d7e9f1a2_gap_assessments.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))

    # ------------------------------------------------------------------
    # 2. Relax sync_logs.version_id to nullable (new-model releases have
    #    no legacy Version row; existing rows keep their non-NULL value).
    # ------------------------------------------------------------------
    op.alter_column(
        "sync_logs",
        "version_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # 3. Add sync_logs.curriculum_version_id (nullable FK → curriculum_versions).
    # ------------------------------------------------------------------
    op.add_column(
        "sync_logs",
        sa.Column(
            "curriculum_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_sync_logs_curriculum_version_id",
        "sync_logs",
        "curriculum_versions",
        ["curriculum_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sync_logs_curriculum_version_id",
        "sync_logs",
        ["curriculum_version_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ------------------------------------------------------------------
    # 3. Reverse sync_logs.curriculum_version_id.
    # ------------------------------------------------------------------
    op.drop_index("ix_sync_logs_curriculum_version_id", table_name="sync_logs")
    op.drop_constraint(
        "fk_sync_logs_curriculum_version_id", "sync_logs", type_="foreignkey"
    )
    op.drop_column("sync_logs", "curriculum_version_id")

    # ------------------------------------------------------------------
    # 2. Restore sync_logs.version_id NOT NULL.
    # ------------------------------------------------------------------
    op.alter_column(
        "sync_logs",
        "version_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # 1. Drop RLS → indexes → sync_targets table.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index("ix_sync_targets_curriculum_id", table_name="sync_targets")
    op.drop_index("ix_sync_targets_organization_id", table_name="sync_targets")
    op.drop_table("sync_targets")
