"""ccr change_set

Stores the structured executable change-set authored on a ChangeRequest so the
PR-style merge endpoint can replay it through fork() at merge time. Plain JSONB
column on an existing tenant-scoped table (no new RLS table).

Revision ID: a8c0e2f4b6d8
Revises: f6b8d0e2a4c7
Create Date: 2026-06-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a8c0e2f4b6d8'
down_revision: Union[str, Sequence[str], None] = 'f6b8d0e2a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'change_requests',
        sa.Column('change_set', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_requests', 'change_set')
