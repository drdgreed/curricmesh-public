"""tutor_conversations + tutor_messages tables (Phase B, B3 — RAG tutor store)

Adds the tenant-scoped conversation store for the Phase B RAG Q&A tutor: the
D5 "secure server-side record" — the FULL un-redacted learner question + the
tutor's grounded answer + its citations. Two tables:

* ``tutor_conversations`` — one thread pinned to an ``enrollments`` row.
* ``tutor_messages``      — one turn (role learner|tutor), text, citations JSONB.

Both join the tenant-isolation RLS regime. Mirrors
``e2a4c6f8b0d1_learner_delivery_tables.py`` — plain tables + ``_enable_sql`` over
a hardcoded table tuple (P-009). Revises ``b7d9f1a3c5e2`` (content_chunks) so it
stacks cleanly on the Phase-B retrieval baseline.

Revision ID: c4e6a8b0d2f4
Revises: b7d9f1a3c5e2
Create Date: 2026-07-07 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.rls import _disable_sql, _enable_sql


# revision identifiers, used by Alembic.
revision: str = 'c4e6a8b0d2f4'
down_revision: Union[str, Sequence[str], None] = 'd3f5b7a9c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded per-migration table list (P-009: never iterate a live/growing list).
_TABLES = ("tutor_conversations", "tutor_messages")


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. tutor_conversations (tenant-scoped, one thread per enrollment).
    # ------------------------------------------------------------------
    op.create_table(
        "tutor_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            ["enrollment_id"], ["enrollments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tutor_conversations_organization_id",
        "tutor_conversations",
        ["organization_id"],
    )
    op.create_index(
        "ix_tutor_conversations_enrollment_id",
        "tutor_conversations",
        ["enrollment_id"],
    )

    # ------------------------------------------------------------------
    # 2. tutor_messages (tenant-scoped, one turn per row + citations JSONB).
    # ------------------------------------------------------------------
    op.create_table(
        "tutor_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["conversation_id"],
            ["tutor_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tutor_messages_organization_id",
        "tutor_messages",
        ["organization_id"],
    )
    op.create_index(
        "ix_tutor_messages_conversation_id",
        "tutor_messages",
        ["conversation_id"],
    )

    # ------------------------------------------------------------------
    # 3. RLS: join the tenant-isolation regime (P-009: hardcoded list only).
    # ------------------------------------------------------------------
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
        "ix_tutor_messages_conversation_id", table_name="tutor_messages"
    )
    op.drop_index(
        "ix_tutor_messages_organization_id", table_name="tutor_messages"
    )
    op.drop_table("tutor_messages")
    op.drop_index(
        "ix_tutor_conversations_enrollment_id", table_name="tutor_conversations"
    )
    op.drop_index(
        "ix_tutor_conversations_organization_id", table_name="tutor_conversations"
    )
    op.drop_table("tutor_conversations")
