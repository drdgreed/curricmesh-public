"""curriculum active_content_version_id (new-model active pointer)

Milestone 4 (``fork()``) introduces the immutable content model's own "which
version is live" pointer:

* ``curricula.active_content_version_id`` — a nullable FK → ``curriculum_versions.id``.

It is **nullable** so this migration is purely additive and back-compatible:
every existing (back-filled) curriculum keeps ``active_content_version_id = NULL``
and continues to resolve its active manifest via the legacy semver bridge in
``app.core.manifest.active_curriculum_version`` (unchanged output → golden tests
still pass). ``fork()`` sets this pointer (via an optimistic compare-and-swap)
when it activates a new version, at which point the manifest resolver prefers it
over the semver bridge.

Mirrors the existing ``curricula.current_version_id`` column exactly: **no
DB-level FK**. A real FK to ``curriculum_versions.id`` would close a cycle
(``curriculum_versions.curriculum_id -> curricula.id`` and back), which breaks
SQLAlchemy's metadata-level ``create_all``/``drop_all`` topological sort that the
test harness relies on. Referential integrity is enforced at the application
layer (``fork()`` only ever points this at a version it just created), the same
trade-off the legacy pointer makes.

Round-trips: ``downgrade base && upgrade head`` clean on a fresh schema (P-009).

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b5
Create Date: 2026-06-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f6b8d0e2a4c7'
down_revision: Union[str, Sequence[str], None] = 'e5a7c9d1f3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable new-model active pointer to ``curricula`` (no DB FK)."""
    op.add_column(
        "curricula",
        sa.Column(
            "active_content_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the new-model active pointer."""
    op.drop_column("curricula", "active_content_version_id")
