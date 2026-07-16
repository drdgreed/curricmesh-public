"""content_chunks table + pgvector extension (Phase B retrieval infra)

Adds the ``content_chunks`` tenant-scoped table for the Phase B RAG tutor's
retrieval layer: version-pinned, embeddable slices of a released curriculum
version's material. Enables the ``vector`` extension, creates the table with a
``Vector(EMBEDDING_DIM)`` embedding column + an HNSW cosine ANN index, and joins
the tenant-isolation RLS regime.

Mirrors ``e1b3d5f7a9c2_media_assets.py`` — _enable_sql/_disable_sql over a
hardcoded 1-tuple (P-009). Revises the media_refs head so this stacks cleanly on
Foundation 1's baseline.

Revision ID: b7d9f1a3c5e2
Revises: a3b5c7d9e1f2
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'b7d9f1a3c5e2'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6f8b0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("content_chunks",)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 0. pgvector extension (required by the Vector column + ANN index).
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # 1. Create content_chunks (tenant-scoped, Phase B retrieval infra).
    #    The embedding column width is fixed here at EMBEDDING_DIM.
    # ------------------------------------------------------------------
    op.create_table(
        "content_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(settings.EMBEDDING_DIM), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
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
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_member_id"], ["version_members.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_content_chunks_organization_id",
        "content_chunks",
        ["organization_id"],
    )
    op.create_index(
        "ix_content_chunks_curriculum_version_id",
        "content_chunks",
        ["curriculum_version_id"],
    )
    op.create_index(
        "ix_content_chunks_source_member_id",
        "content_chunks",
        ["source_member_id"],
    )
    # HNSW ANN index for cosine-distance (``<=>``) top-k retrieval. Raw SQL so
    # the operator-class clause renders cleanly in offline (--sql) mode too.
    op.execute(
        "CREATE INDEX ix_content_chunks_embedding_hnsw ON content_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # 2. RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    #    Mirror e1b3d5f7a9c2_media_assets.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    # ------------------------------------------------------------------
    # Drop RLS → indexes → content_chunks table. (Leave the vector extension
    # in place — other objects may depend on it; dropping it is not our call.)
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.execute("DROP INDEX IF EXISTS ix_content_chunks_embedding_hnsw")
    op.drop_index(
        "ix_content_chunks_source_member_id", table_name="content_chunks"
    )
    op.drop_index(
        "ix_content_chunks_curriculum_version_id", table_name="content_chunks"
    )
    op.drop_index(
        "ix_content_chunks_organization_id", table_name="content_chunks"
    )
    op.drop_table("content_chunks")
