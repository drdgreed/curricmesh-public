"""learner delivery tables (enrollments, learner_progress, assessment_submissions)

Adds the three tenant-scoped learner-delivery tables for Phase 2 Foundation 1
(self-paced individual delivery). Mirrors ``e1b3d5f7a9c2_media_assets.py``
exactly — create_table + indexes, then _enable_sql/_disable_sql over a hardcoded
tuple (P-009: never iterate a live/growing list).

Revision ID: e2a4c6f8b0d1
Revises: a3b5c7d9e1f2
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'e2a4c6f8b0d1'
down_revision: Union[str, Sequence[str], None] = 'b7d9f1a3c5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("enrollments", "learner_progress", "assessment_submissions")


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. enrollments — a learner pinned to a released CurriculumVersion.
    # ------------------------------------------------------------------
    op.create_table(
        "enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("learner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "learner_id",
            "curriculum_version_id",
            name="uq_enrollments_learner_version",
        ),
    )
    op.create_index(
        "ix_enrollments_organization_id", "enrollments", ["organization_id"]
    )
    op.create_index("ix_enrollments_learner_id", "enrollments", ["learner_id"])
    op.create_index(
        "ix_enrollments_curriculum_version_id",
        "enrollments",
        ["curriculum_version_id"],
    )

    # ------------------------------------------------------------------
    # 2. learner_progress — one row per learner per item.
    # ------------------------------------------------------------------
    op.create_table(
        "learner_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="not_started"
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["content_member_id"], ["version_members.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enrollment_id",
            "content_member_id",
            name="uq_learner_progress_enrollment_member",
        ),
    )
    op.create_index(
        "ix_learner_progress_organization_id",
        "learner_progress",
        ["organization_id"],
    )
    op.create_index(
        "ix_learner_progress_enrollment_id", "learner_progress", ["enrollment_id"]
    )
    op.create_index(
        "ix_learner_progress_content_member_id",
        "learner_progress",
        ["content_member_id"],
    )

    # ------------------------------------------------------------------
    # 3. assessment_submissions — a learner's answer to an assessment item.
    # ------------------------------------------------------------------
    op.create_table(
        "assessment_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["content_member_id"], ["version_members.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_submissions_organization_id",
        "assessment_submissions",
        ["organization_id"],
    )
    op.create_index(
        "ix_assessment_submissions_enrollment_id",
        "assessment_submissions",
        ["enrollment_id"],
    )
    op.create_index(
        "ix_assessment_submissions_content_member_id",
        "assessment_submissions",
        ["content_member_id"],
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
    # Drop RLS → indexes → tables (reverse creation order).
    # ------------------------------------------------------------------
    bind = op.get_bind()
    for table in reversed(_TABLES):
        for stmt in _disable_sql(table):
            bind.execute(sa.text(stmt))

    op.drop_index(
        "ix_assessment_submissions_content_member_id",
        table_name="assessment_submissions",
    )
    op.drop_index(
        "ix_assessment_submissions_enrollment_id",
        table_name="assessment_submissions",
    )
    op.drop_index(
        "ix_assessment_submissions_organization_id",
        table_name="assessment_submissions",
    )
    op.drop_table("assessment_submissions")

    op.drop_index(
        "ix_learner_progress_content_member_id", table_name="learner_progress"
    )
    op.drop_index(
        "ix_learner_progress_enrollment_id", table_name="learner_progress"
    )
    op.drop_index(
        "ix_learner_progress_organization_id", table_name="learner_progress"
    )
    op.drop_table("learner_progress")

    op.drop_index(
        "ix_enrollments_curriculum_version_id", table_name="enrollments"
    )
    op.drop_index("ix_enrollments_learner_id", table_name="enrollments")
    op.drop_index("ix_enrollments_organization_id", table_name="enrollments")
    op.drop_table("enrollments")
