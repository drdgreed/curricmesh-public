"""deck_artifacts table (Slide System Port, S4 — deck ↔ course linkage)

Adds the ``deck_artifacts`` tenant-scoped table that links a rendered deck's
three R2 artifacts (``deck.{pdf,pptx,html}`` from S1) to a released
``CurriculumVersion`` — optionally to the ``VersionMember`` it was generated
from — so the learner-facing serve endpoint can find a version's decks and mint
fresh presigned GET URLs. Joins the tenant-isolation RLS regime.

This is the ONE migration in the slide port (S2's deck generator adds no schema),
so no chain conflict is expected. Mirrors ``d3f5b7a9c1e4_generation_jobs.py`` —
``_enable_sql``/``_disable_sql`` over a hardcoded 1-tuple (P-009). Revises the
current head (``c4e6a8b0d2f4``) so it stacks cleanly.

Revision ID: e7a9c1b3d5f6
Revises: c4e6a8b0d2f4
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'e7a9c1b3d5f6'
down_revision: Union[str, Sequence[str], None] = 'c4e6a8b0d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("deck_artifacts",)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "deck_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pdf_key", sa.String(length=1024), nullable=False),
        sa.Column("pptx_key", sa.String(length=1024), nullable=False),
        sa.Column("html_key", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
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
            ["source_member_id"], ["version_members.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deck_artifacts_organization_id",
        "deck_artifacts",
        ["organization_id"],
    )
    op.create_index(
        "ix_deck_artifacts_curriculum_version_id",
        "deck_artifacts",
        ["curriculum_version_id"],
    )
    op.create_index(
        "ix_deck_artifacts_source_member_id",
        "deck_artifacts",
        ["source_member_id"],
    )

    # RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index(
        "ix_deck_artifacts_source_member_id", table_name="deck_artifacts"
    )
    op.drop_index(
        "ix_deck_artifacts_curriculum_version_id", table_name="deck_artifacts"
    )
    op.drop_index(
        "ix_deck_artifacts_organization_id", table_name="deck_artifacts"
    )
    op.drop_table("deck_artifacts")
