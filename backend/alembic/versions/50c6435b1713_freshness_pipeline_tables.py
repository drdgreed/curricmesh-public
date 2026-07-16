"""freshness pipeline tables

Adds the four tenant-scoped tables for the biweekly freshness pipeline
(Phase 1):

  * ``source_watch_items``      — university/institution syllabus URLs to watch
  * ``syllabus_snapshots``      — per-item captured snapshots (JSONB topics + hash)
  * ``freshness_pipeline_seen`` — per-org signal dedup ledger (unique signal_id × org)
  * ``freshness_pipeline_runs`` — per-org run audit records

All four are ``TenantScoped`` so this migration follows P-009 exactly: RLS is
applied only to the tables created HERE (hardcoded list), never via the
module-level ``_ORG_SCOPED`` live list.  The RLS pattern mirrors
``d4f6a8c0e1b3_sync_logs.py`` — ``_enable_sql`` / ``_disable_sql`` from
``app.db.rls`` called per table.

Round-trips: upgrade → downgrade → upgrade on a clean schema.

Revision ID: 50c6435b1713
Revises: c7e1a9f4d2b6
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = '50c6435b1713'
down_revision: Union[str, Sequence[str], None] = 'c7e1a9f4d2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = (
    "source_watch_items",
    "syllabus_snapshots",
    "freshness_pipeline_seen",
    "freshness_pipeline_runs",
)


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. source_watch_items
    # ------------------------------------------------------------------
    op.create_table(
        "source_watch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("institution", sa.String(length=256), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("search_hint", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
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
        "ix_source_watch_items_organization_id",
        "source_watch_items",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # 2. syllabus_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "syllabus_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("watch_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_summary", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "confidence",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'fetched'"),
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["watch_item_id"], ["source_watch_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_syllabus_snapshots_organization_id",
        "syllabus_snapshots",
        ["organization_id"],
    )
    op.create_index(
        "ix_syllabus_snapshots_watch_item_id",
        "syllabus_snapshots",
        ["watch_item_id"],
    )

    # ------------------------------------------------------------------
    # 3. freshness_pipeline_seen
    # ------------------------------------------------------------------
    op.create_table(
        "freshness_pipeline_seen",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_id", sa.Text, nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "signal_id", "organization_id", name="uq_pipeline_seen_signal_org"
        ),
    )
    op.create_index(
        "ix_freshness_pipeline_seen_organization_id",
        "freshness_pipeline_seen",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # 4. freshness_pipeline_runs
    # ------------------------------------------------------------------
    op.create_table(
        "freshness_pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column(
            "stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "digest_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_freshness_pipeline_runs_organization_id",
        "freshness_pipeline_runs",
        ["organization_id"],
    )

    # ------------------------------------------------------------------
    # RLS: join the tenant-isolation regime (P-009: hardcoded list only)
    # Mirror d4f6a8c0e1b3_sync_logs.py pattern exactly.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in _TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    # Drop RLS policies first (they reference organization_id), then tables.
    # Reverse table order to respect FK dependencies.
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index(
        "ix_freshness_pipeline_runs_organization_id",
        table_name="freshness_pipeline_runs",
    )
    op.drop_table("freshness_pipeline_runs")

    op.drop_index(
        "ix_freshness_pipeline_seen_organization_id",
        table_name="freshness_pipeline_seen",
    )
    op.drop_table("freshness_pipeline_seen")

    op.drop_index(
        "ix_syllabus_snapshots_watch_item_id", table_name="syllabus_snapshots"
    )
    op.drop_index(
        "ix_syllabus_snapshots_organization_id", table_name="syllabus_snapshots"
    )
    op.drop_table("syllabus_snapshots")

    op.drop_index(
        "ix_source_watch_items_organization_id", table_name="source_watch_items"
    )
    op.drop_table("source_watch_items")
