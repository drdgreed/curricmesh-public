"""version pins

Adds the ``version_pins`` table (V3-B student-portfolio version-pinning) and
applies the tenant-isolation RLS policy to it. ``version_pins`` is tenant-scoped
(``TenantScoped``), so it joins the same fail-closed RLS regime as the other
domain tables — the policy SQL is reused from ``app.db.rls`` rather than
duplicated.

Round-trips: upgrade -> downgrade -> upgrade on a clean schema.

Revision ID: c3e5a7b9c1d3
Revises: b2d4f6a8c0e1
Create Date: 2026-06-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'c3e5a7b9c1d3'
down_revision: Union[str, Sequence[str], None] = 'b2d4f6a8c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "version_pins"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("student_label", sa.String(length=255), nullable=False),
        sa.Column("student_email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["curriculum_id"], ["curricula.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cohort_id"], ["cohorts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{_TABLE}_organization_id", _TABLE, ["organization_id"])
    op.create_index(f"ix_{_TABLE}_curriculum_id", _TABLE, ["curriculum_id"])
    op.create_index(f"ix_{_TABLE}_version_id", _TABLE, ["version_id"])

    # Join the tenant-isolation RLS regime (same fail-closed policy as the other
    # domain tables) — reuse the single-source-of-truth SQL from app.db.rls.
    bind = op.get_bind()
    for stmt in _enable_sql(_TABLE, "organization_id"):
        bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    # Drop the policy first (it references organization_id), then the table.
    for stmt in _disable_sql(_TABLE):
        bind.execute(sa.text(stmt))

    op.drop_index(f"ix_{_TABLE}_version_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_curriculum_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_organization_id", table_name=_TABLE)
    op.drop_table(_TABLE)
