"""gap_assessments table

Adds the ``gap_assessments`` tenant-scoped table for the freshness-pipeline
Judge (Phase 2).  One row per (curriculum, topic, org) — simultaneously the
monitor queue, reject memory, promotion record, and judgment audit trail.

Mirrors ``50c6435b1713_freshness_pipeline_tables.py`` exactly:
- organization_id column/FK/index follows the TenantScoped convention.
- RLS applied via ``_enable_sql``/``_disable_sql`` over a hardcoded 1-tuple
  (P-009: never iterate a live/growing list).
- Downgrade drops RLS → indexes → table.

Revision ID: b3c5d7e9f1a2
Revises: 50c6435b1713
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'b3c5d7e9f1a2'
down_revision: Union[str, Sequence[str], None] = '50c6435b1713'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("gap_assessments",)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gap_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("display_topic", sa.Text, nullable=False),
        sa.Column("recommendation", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("dossier", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("times_seen", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("times_seen_at_last_eval", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("promoted_ccr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_evaluated_at",
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
        sa.ForeignKeyConstraint(
            ["promoted_ccr_id"], ["change_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "curriculum_id", "topic", "organization_id",
            name="uq_gap_assessment_curriculum_topic_org",
        ),
    )
    op.create_index(
        "ix_gap_assessments_organization_id",
        "gap_assessments",
        ["organization_id"],
    )
    op.create_index(
        "ix_gap_assessments_curriculum_id",
        "gap_assessments",
        ["curriculum_id"],
    )

    # ------------------------------------------------------------------
    # RLS: join the tenant-isolation regime (P-009: hardcoded list only)
    # Mirror 50c6435b1713_freshness_pipeline_tables.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    # Drop RLS policies first (they reference organization_id), then table.
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index("ix_gap_assessments_curriculum_id", table_name="gap_assessments")
    op.drop_index("ix_gap_assessments_organization_id", table_name="gap_assessments")
    op.drop_table("gap_assessments")
