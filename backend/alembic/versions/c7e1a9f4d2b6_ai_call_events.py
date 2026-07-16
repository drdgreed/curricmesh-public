"""ai call events

Adds the GLOBAL AI-call telemetry table ``ai_call_events`` — one persisted row
per AI call (model, feature, tokens, cost, latency, stop reason, org), written
best-effort off the AI hot path. This is operational telemetry, NOT a domain
table: it is intentionally NOT tenant-scoped and NOT RLS'd (cross-tenant staff
spend/latency analytics), and its ``organization_id`` is a plain indexed
nullable column with NO foreign key (telemetry stays decoupled from the org
lifecycle). Per P-009 this migration therefore does NOT call ``apply_rls`` and
adds the table to NO frozen RLS list — it just creates the table + its indexes.

Round-trips: ``upgrade head`` then ``downgrade -1`` then ``upgrade head`` clean.

Revision ID: c7e1a9f4d2b6
Revises: 194259011028
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1a9f4d2b6'
down_revision: Union[str, Sequence[str], None] = '194259011028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_call_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('feature', sa.String(length=64), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('stop_reason', sa.String(length=32), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_ai_call_events_organization_id'),
        'ai_call_events',
        ['organization_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_ai_call_events_created_at'),
        'ai_call_events',
        ['created_at'],
        unique=False,
    )
    op.create_index(
        'ix_ai_call_events_org_created',
        'ai_call_events',
        ['organization_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ai_call_events_org_created', table_name='ai_call_events')
    op.drop_index(op.f('ix_ai_call_events_created_at'), table_name='ai_call_events')
    op.drop_index(
        op.f('ix_ai_call_events_organization_id'), table_name='ai_call_events'
    )
    op.drop_table('ai_call_events')
