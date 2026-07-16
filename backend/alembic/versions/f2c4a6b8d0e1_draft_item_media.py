"""draft_item_media link table

Adds the ``draft_item_media`` tenant-scoped join table for the Authoring
Platform (slice 2: media in content) — associates an owned ``MediaAsset`` with
a ``DraftItem``. Mirrors ``e1b3d5f7a9c2_media_assets.py`` exactly —
_enable_sql/_disable_sql over a hardcoded 1-tuple (P-009).

Revision ID: f2c4a6b8d0e1
Revises: e1b3d5f7a9c2
Create Date: 2026-07-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'f2c4a6b8d0e1'
down_revision: Union[str, Sequence[str], None] = 'e1b3d5f7a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("draft_item_media",)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. Create draft_item_media (tenant-scoped, Authoring Platform slice 2).
    # ------------------------------------------------------------------
    op.create_table(
        "draft_item_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
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
            ["draft_item_id"], ["draft_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_item_id",
            "media_asset_id",
            name="uq_draft_item_media_item_asset",
        ),
    )
    op.create_index(
        "ix_draft_item_media_organization_id",
        "draft_item_media",
        ["organization_id"],
    )
    op.create_index(
        "ix_draft_item_media_draft_item_id",
        "draft_item_media",
        ["draft_item_id"],
    )
    op.create_index(
        "ix_draft_item_media_media_asset_id",
        "draft_item_media",
        ["media_asset_id"],
    )

    # ------------------------------------------------------------------
    # RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    # Mirror e1b3d5f7a9c2_media_assets.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    # ------------------------------------------------------------------
    # Drop RLS → indexes → draft_item_media table.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index(
        "ix_draft_item_media_media_asset_id", table_name="draft_item_media"
    )
    op.drop_index(
        "ix_draft_item_media_draft_item_id", table_name="draft_item_media"
    )
    op.drop_index(
        "ix_draft_item_media_organization_id", table_name="draft_item_media"
    )
    op.drop_table("draft_item_media")
