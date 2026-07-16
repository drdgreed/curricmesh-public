"""immutable content model

Adds the five tables of the immutable, content-addressed version model
(``docs/specs/2026-06-06-immutable-version-model-design.md`` §3) **alongside**
the legacy ``assets``/``asset_versions``/``versions`` model — purely additive,
nothing reads them yet:

* ``lineage_assets``     — version-independent logical assets.
* ``content_versions``   — immutable, append-only content blobs (content_hash).
* ``curriculum_versions``— lightweight per-curriculum manifests (semver/status).
* ``version_members``    — which content revision of which asset is in a version.
* ``version_edges``      — version-scoped prerequisite edges on logical assets.

All five are tenant-scoped, so each joins the fail-closed RLS regime. Per the
P-009 frozen-snapshot discipline this migration applies RLS **only to the five
tables it creates**, via the per-table ``_enable_sql`` helper — it does NOT call
the live-list ``apply_rls`` (which would, replayed from base, try to enable RLS
on tables a later migration would otherwise add to the live list).

Round-trips: ``downgrade base && upgrade head`` clean on a fresh schema.

Revision ID: e5a7c9d1f3b5
Revises: d4f6a8c0e1b3
Create Date: 2026-06-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'e5a7c9d1f3b5'
down_revision: Union[str, Sequence[str], None] = 'd4f6a8c0e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen snapshot: the tables THIS migration creates + RLS-protects. Hardcoded
# (not read from the live app.db.rls list) per P-009 so a from-base replay only
# ever touches tables that exist at this point in history.
_NEW_TABLES = (
    "lineage_assets",
    "content_versions",
    "curriculum_versions",
    "version_members",
    "version_edges",
)

# The native PG enum types these tables reference. They already exist (created
# by the initial schema for the legacy model), so the enum columns must NOT
# re-emit a CREATE TYPE — use ``create_type=False``.
_assetkind = postgresql.ENUM(name="assetkind", create_type=False)
_lifecyclestatus = postgresql.ENUM(name="lifecyclestatus", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "lineage_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", _assetkind, nullable=False),
        sa.Column("lineage_key", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lineage_assets_organization_id", "lineage_assets", ["organization_id"])

    op.create_table(
        "content_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["lineage_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "seq", name="uq_content_versions_asset_seq"),
    )
    op.create_index("ix_content_versions_organization_id", "content_versions", ["organization_id"])
    op.create_index("ix_content_versions_asset_id", "content_versions", ["asset_id"])

    op.create_table(
        "curriculum_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("minor", sa.Integer(), nullable=False),
        sa.Column("patch", sa.Integer(), nullable=False),
        sa.Column("status", _lifecyclestatus, nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["curriculum_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_curriculum_versions_organization_id", "curriculum_versions", ["organization_id"]
    )
    op.create_index(
        "ix_curriculum_versions_curriculum_id", "curriculum_versions", ["curriculum_id"]
    )

    op.create_table(
        "version_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=False),
        sa.Column("week_index", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["lineage_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["asset_version_id"], ["content_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_version_members_organization_id", "version_members", ["organization_id"]
    )
    op.create_index(
        "ix_version_members_curriculum_version_id",
        "version_members",
        ["curriculum_version_id"],
    )

    op.create_table(
        "version_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.String(length=128), nullable=False),
        sa.Column("validated_against_seq", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["from_asset_id"], ["lineage_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_asset_id"], ["lineage_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_version_edges_organization_id", "version_edges", ["organization_id"]
    )
    op.create_index(
        "ix_version_edges_curriculum_version_id",
        "version_edges",
        ["curriculum_version_id"],
    )

    # Join the tenant-isolation RLS regime — frozen per-table snapshot (P-009).
    bind = op.get_bind()
    for table in _NEW_TABLES:
        for stmt in _enable_sql(table, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    # Drop the policies first (they reference organization_id) — reverse order.
    for table in reversed(_NEW_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index("ix_version_edges_curriculum_version_id", table_name="version_edges")
    op.drop_index("ix_version_edges_organization_id", table_name="version_edges")
    op.drop_table("version_edges")

    op.drop_index(
        "ix_version_members_curriculum_version_id", table_name="version_members"
    )
    op.drop_index("ix_version_members_organization_id", table_name="version_members")
    op.drop_table("version_members")

    op.drop_index(
        "ix_curriculum_versions_curriculum_id", table_name="curriculum_versions"
    )
    op.drop_index(
        "ix_curriculum_versions_organization_id", table_name="curriculum_versions"
    )
    op.drop_table("curriculum_versions")

    op.drop_index("ix_content_versions_asset_id", table_name="content_versions")
    op.drop_index("ix_content_versions_organization_id", table_name="content_versions")
    op.drop_table("content_versions")

    op.drop_index("ix_lineage_assets_organization_id", table_name="lineage_assets")
    op.drop_table("lineage_assets")
