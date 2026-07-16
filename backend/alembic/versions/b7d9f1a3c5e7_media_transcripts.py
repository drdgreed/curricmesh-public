"""media_transcripts table

Adds the ``media_transcripts`` tenant-scoped table for the Phase B RAG tutor
(slice B2: media transcription pipeline). One transcript per asset
(``media_asset_id`` UNIQUE); a re-transcribe replaces the row. Mirrors
``e1b3d5f7a9c2_media_assets.py`` exactly — _enable_sql/_disable_sql over a
hardcoded 1-tuple (P-009).

Revision ID: b7d9f1a3c5e7
Revises: a3b5c7d9e1f2
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'b7d9f1a3c5e7'
down_revision: Union[str, Sequence[str], None] = 'a3b5c7d9e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("media_transcripts",)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. Create media_transcripts (tenant-scoped, Phase B slice B2).
    # ------------------------------------------------------------------
    op.create_table(
        "media_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
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
            ["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_asset_id", name="uq_media_transcripts_media_asset_id"),
    )
    op.create_index(
        "ix_media_transcripts_organization_id",
        "media_transcripts",
        ["organization_id"],
    )
    op.create_index(
        "ix_media_transcripts_media_asset_id",
        "media_transcripts",
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
    # Drop RLS → indexes → media_transcripts table.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index(
        "ix_media_transcripts_media_asset_id", table_name="media_transcripts"
    )
    op.drop_index(
        "ix_media_transcripts_organization_id", table_name="media_transcripts"
    )
    op.drop_table("media_transcripts")
