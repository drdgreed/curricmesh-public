"""content_versions.media_refs (frozen media pins)

Adds a nullable ``media_refs`` JSONB column to ``content_versions`` for the
Authoring Platform (slice 2: media in content). On release, publish snapshots
the media assets an item references into this list so a released
``CurriculumVersion`` pins the exact assets it shipped with. No RLS change —
``content_versions`` is already tenant-scoped.

Revision ID: a3b5c7d9e1f2
Revises: f2c4a6b8d0e1
Create Date: 2026-07-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3b5c7d9e1f2'
down_revision: Union[str, Sequence[str], None] = 'f2c4a6b8d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "content_versions",
        sa.Column("media_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("content_versions", "media_refs")
