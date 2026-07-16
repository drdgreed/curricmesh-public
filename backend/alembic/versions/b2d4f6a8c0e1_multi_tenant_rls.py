"""multi tenant rls

Adds ``organization_id`` to the 13 curriculum-domain tables, backfills it down
the ownership tree, makes it NOT NULL, and applies the tenant-isolation RLS
policies (reusing ``app.db.rls`` — no duplicated policy SQL).

Round-trips: upgrade -> downgrade -> upgrade on a clean schema. The backfill is
defensive: every statement is valid SQL that affects 0 rows on an empty DB and
correctly cascades the org on a populated DB.

Revision ID: b2d4f6a8c0e1
Revises: a1c3d5e7f9b0
Create Date: 2026-06-04 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Use the per-table DDL helpers (NOT apply_rls/drop_rls, which iterate the LIVE
# growing _ORG_SCOPED list). A migration must apply RLS only to the tables that
# exist at ITS point in history — freeze to this migration's own _DOMAIN_TABLES,
# else replaying from base breaks once later migrations add new scoped tables.
from app.db.rls import _enable_sql, _disable_sql


# revision identifiers, used by Alembic.
revision: str = 'b2d4f6a8c0e1'
down_revision: Union[str, Sequence[str], None] = 'a1c3d5e7f9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The 13 curriculum-domain tables that gain ``organization_id``. Kept in sync
# with app.db.rls._ORG_SCOPED (the RLS contract); NOT users/organizations/
# sota_sources.
_DOMAIN_TABLES: tuple[str, ...] = (
    "curricula", "versions", "cohorts", "modules", "projects", "assets",
    "asset_versions", "change_requests", "qa_reviews", "approvals",
    "dependency_edges", "sota_findings", "history_events",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. Add the nullable column + index + FK on every domain table. Nullable
    #    for now so the backfill can run before we tighten to NOT NULL.
    for t in _DOMAIN_TABLES:
        op.add_column(
            t,
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(f"ix_{t}_organization_id", t, ["organization_id"])
        op.create_foreign_key(
            f"fk_{t}_organization_id",
            t,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # 2. Backfill. Every statement below is valid SQL that affects 0 rows on an
    #    empty DB and cascades the org down the ownership tree on a populated DB.

    # Bootstrap a single legacy org ONLY if there are unscoped curricula
    # (gen_random_uuid() ships with postgres 16 — no extension needed).
    bind.execute(sa.text(
        """
        INSERT INTO organizations (id, name, created_at)
        SELECT gen_random_uuid(), 'Legacy (migrated)', now()
        WHERE EXISTS (SELECT 1 FROM curricula WHERE organization_id IS NULL)
        """
    ))

    # Root: curricula <- the legacy org.
    bind.execute(sa.text(
        """
        UPDATE curricula
        SET organization_id = (
            SELECT id FROM organizations WHERE name = 'Legacy (migrated)' LIMIT 1
        )
        WHERE organization_id IS NULL
        """
    ))

    # Direct children of curricula (via curriculum_id).
    for child in ("versions", "cohorts", "change_requests", "sota_findings"):
        bind.execute(sa.text(
            f"""
            UPDATE {child} AS c
            SET organization_id = cur.organization_id
            FROM curricula AS cur
            WHERE c.curriculum_id = cur.id
              AND c.organization_id IS NULL
            """
        ))

    # modules / projects <- versions (via version_id).
    for child in ("modules", "projects"):
        bind.execute(sa.text(
            f"""
            UPDATE {child} AS c
            SET organization_id = v.organization_id
            FROM versions AS v
            WHERE c.version_id = v.id
              AND c.organization_id IS NULL
            """
        ))

    # assets <- modules (preferred) or projects (fallback), each -> versions.
    bind.execute(sa.text(
        """
        UPDATE assets AS a
        SET organization_id = m.organization_id
        FROM modules AS m
        WHERE a.module_id = m.id
          AND a.organization_id IS NULL
        """
    ))
    bind.execute(sa.text(
        """
        UPDATE assets AS a
        SET organization_id = p.organization_id
        FROM projects AS p
        WHERE a.project_id = p.id
          AND a.organization_id IS NULL
        """
    ))

    # asset_versions <- assets (via asset_id).
    bind.execute(sa.text(
        """
        UPDATE asset_versions AS av
        SET organization_id = a.organization_id
        FROM assets AS a
        WHERE av.asset_id = a.id
          AND av.organization_id IS NULL
        """
    ))

    # qa_reviews / approvals <- change_requests (via ccr_id).
    for child in ("qa_reviews", "approvals"):
        bind.execute(sa.text(
            f"""
            UPDATE {child} AS c
            SET organization_id = cr.organization_id
            FROM change_requests AS cr
            WHERE c.ccr_id = cr.id
              AND c.organization_id IS NULL
            """
        ))

    # dependency_edges <- an endpoint asset (from_asset_id preferred, else to).
    bind.execute(sa.text(
        """
        UPDATE dependency_edges AS d
        SET organization_id = a.organization_id
        FROM assets AS a
        WHERE d.from_asset_id = a.id
          AND d.organization_id IS NULL
        """
    ))
    bind.execute(sa.text(
        """
        UPDATE dependency_edges AS d
        SET organization_id = a.organization_id
        FROM assets AS a
        WHERE d.to_asset_id = a.id
          AND d.organization_id IS NULL
        """
    ))

    # history_events <- the actor user's org if resolvable.
    bind.execute(sa.text(
        """
        UPDATE history_events AS h
        SET organization_id = u.organization_id
        FROM users AS u
        WHERE h.actor_id = u.id
          AND u.organization_id IS NOT NULL
          AND h.organization_id IS NULL
        """
    ))
    # ... else the legacy org (actor null/unscoped). Only fires when a legacy
    # org exists (i.e. there were unscoped curricula to migrate).
    bind.execute(sa.text(
        """
        UPDATE history_events
        SET organization_id = (
            SELECT id FROM organizations WHERE name = 'Legacy (migrated)' LIMIT 1
        )
        WHERE organization_id IS NULL
        """
    ))

    # 3. Tighten to NOT NULL now that every row is scoped.
    for t in _DOMAIN_TABLES:
        op.alter_column(t, "organization_id", nullable=False)

    # 4. Apply tenant-isolation RLS to THIS migration's 13 tables (frozen list;
    #    later migrations apply RLS to their own new tables).
    for t in _DOMAIN_TABLES:
        for stmt in _enable_sql(t, "organization_id"):
            bind.execute(sa.text(stmt))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    # Drop RLS first — its policies reference organization_id, so the policies
    # must go before the columns they depend on. Frozen to this migration's tables.
    for t in _DOMAIN_TABLES:
        for stmt in _disable_sql(t):
            bind.execute(sa.text(stmt))

    # Reverse of the add loop: FK, then index, then column.
    for t in reversed(_DOMAIN_TABLES):
        op.drop_constraint(f"fk_{t}_organization_id", t, type_="foreignkey")
        op.drop_index(f"ix_{t}_organization_id", table_name=t)
        op.drop_column(t, "organization_id")

    # Intentionally leave the 'Legacy (migrated)' org row in place.
