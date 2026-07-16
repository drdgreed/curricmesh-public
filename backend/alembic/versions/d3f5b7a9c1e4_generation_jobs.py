"""generation_jobs table (async course generation)

Adds the ``generation_jobs`` tenant-scoped table backing the async rework of
``POST /api/v1/builder/generate-course``: the endpoint creates a job, schedules
the real orchestration on a background task, and returns 202 + job_id; the
background runner updates the row's progress and terminal state, which clients
poll. Joins the tenant-isolation RLS regime.

Mirrors ``b7d9f1a3c5e2_content_chunks.py`` / ``e1b3d5f7a9c2_media_assets.py`` —
_enable_sql/_disable_sql over a hardcoded 1-tuple (P-009). Revises the current
head (``b7d9f1a3c5e2``) so it stacks cleanly.

Revision ID: d3f5b7a9c1e4
Revises: b7d9f1a3c5e2
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'd3f5b7a9c1e4'
down_revision: Union[str, Sequence[str], None] = 'b7d9f1a3c5e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("generation_jobs",)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("completed_steps", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=128), nullable=True),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["draft_courses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_jobs_organization_id",
        "generation_jobs",
        ["organization_id"],
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
        "ix_generation_jobs_organization_id", table_name="generation_jobs"
    )
    op.drop_table("generation_jobs")
