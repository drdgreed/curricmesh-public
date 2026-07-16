"""media_assets table

Adds the ``media_assets`` tenant-scoped table for the Authoring Platform
(slice 1: owned-media backend). Mirrors ``c4e6f8a0b2d4_sync_targets.py``
exactly — _enable_sql/_disable_sql over a hardcoded 1-tuple (P-009).

Revision ID: e1b3d5f7a9c2
Revises: c4e6f8a0b2d4
Create Date: 2026-07-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'e1b3d5f7a9c2'
down_revision: Union[str, Sequence[str], None] = 'c4e6f8a0b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("media_assets",)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. Create media_assets (tenant-scoped, Authoring Platform slice 1).
    # ------------------------------------------------------------------
    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_assets_organization_id",
        "media_assets",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    # Mirror c4e6f8a0b2d4_sync_targets.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    # ------------------------------------------------------------------
    # Drop RLS → indexes → media_assets table.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index("ix_media_assets_organization_id", table_name="media_assets")
    op.drop_table("media_assets")
