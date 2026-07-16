"""Single source of truth for CurricMesh's Postgres Row-Level Security DDL.

Both the Alembic migration (MT4) and the test conftest (MT3) call into this
module so the tenant-isolation policies are defined exactly once — no SQL
duplication and no drift between production and test wiring.

The isolation contract:

* Every protected table carries an ``organization_id`` column that identifies
  its owning tenant.
* A ``tenant_isolation`` policy restricts both reads and writes to rows whose
  ``organization_id`` equals the per-connection GUC ``app.current_org``.
* ``current_setting('app.current_org', true)`` is *missing-ok*: when the GUC is
  unset it returns NULL, which matches no rows. The policy is therefore
  **fail-closed** — forgetting to set the org context exposes nothing rather
  than everything.

Import-light by design: only ``sqlalchemy`` is imported so this module can be
pulled into a migration's offline/online context without dragging in
``app.models`` or ``app.database`` and risking import cycles.
"""

from __future__ import annotations

import sqlalchemy as sa

# The curriculum-domain tables, all scoped on ``organization_id``.
#
# DELIBERATELY EXCLUDED (MT3 scope refinement, overrides MT2's draft list):
#
# * ``users`` and ``organizations`` are NOT RLS-scoped. They are queried in
#   inherently cross-tenant flows that run *before* any org context exists:
#   login-by-email (resolve a user → mint a token → only then is the org known)
#   and org provisioning (create the organization row itself). Forcing RLS on
#   them would break those flows for zero isolation gain — no endpoint exposes
#   users or organizations across tenants, so there is nothing to leak.
# * ``sota_sources`` stays GLOBAL: a shared corpus of state-of-the-art evidence
#   with no per-tenant ownership. Only ``sota_findings`` (which references a
#   tenant's curriculum) is scoped.
_ORG_SCOPED: tuple[str, ...] = (
    "curricula", "versions", "cohorts", "modules", "projects", "assets",
    "asset_versions", "change_requests", "qa_reviews", "approvals",
    "dependency_edges", "sota_findings", "history_events",
    # V3-B: per-student version-pin provenance (tenant-scoped).
    "version_pins",
    # V3-C: external-sync attempt audit log (tenant-scoped).
    "sync_logs",
    # Foundation (immutable version model): the five new content-addressed
    # tables. All tenant-scoped → they join the same fail-closed RLS regime.
    "lineage_assets",
    "content_versions",
    "curriculum_versions",
    "version_members",
    "version_edges",
    # Course Builder: mutable draft authoring tables. All tenant-scoped → they
    # join the same fail-closed RLS regime.
    "draft_courses",
    "draft_objectives",
    "draft_items",
    "draft_item_objectives",
    "draft_dependencies",
    "draft_rubric_results",
    "draft_advisor_notes",
    # Freshness pipeline (Phase 1): watchlist/snapshots/seen/runs. All
    # tenant-scoped → same fail-closed RLS regime. (Their migration also
    # applies RLS explicitly — this registry covers bulk re-grants/DR.)
    "source_watch_items",
    "syllabus_snapshots",
    "freshness_pipeline_seen",
    "freshness_pipeline_runs",
    # Judge (Phase 2)
    "gap_assessments",
    # Sync (Phase 4): per-tenant outbound-sync destination config.
    "sync_targets",
    # Authoring media (slice 1): tenant-scoped owned-media asset registry.
    "media_assets",
    # Authoring media (slice 2): DraftItem <-> MediaAsset usage link.
    "draft_item_media",
    # Phase B (B2): per-asset extracted/transcribed text.
    "media_transcripts",
    # Learner delivery (Phase 2, Foundation 1): self-paced enrollment, per-item
    # progress, and assessment submissions. All tenant-scoped → same fail-closed
    # RLS regime.
    "enrollments",
    "learner_progress",
    "assessment_submissions",
    # Retrieval infra (Phase B, Foundation 1): version-pinned embeddable chunks.
    "content_chunks",
    # Tutor (Phase B, B3): RAG Q&A conversation store (D5 secure server-side
    # record). Both tenant-scoped → same fail-closed RLS regime.
    "tutor_conversations",
    "tutor_messages",
    # Async course generation: background-job tracking for POST /generate-course.
    "generation_jobs",
    # Slide System Port (S4): rendered deck ↔ released CurriculumVersion linkage.
    "deck_artifacts",
)

POLICY_NAME = "tenant_isolation"


def _scoped_columns() -> dict[str, str]:
    """Return ``{table: scoping_column}`` for every protected table."""
    return {t: "organization_id" for t in _ORG_SCOPED}


# All protected tables, in deterministic insertion order.
TENANT_TABLES: tuple[str, ...] = tuple(_scoped_columns())


def _enable_sql(table: str, col: str) -> list[str]:
    """DDL that enables and forces the tenant-isolation policy on ``table``."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table};",
        (
            f"CREATE POLICY {POLICY_NAME} ON {table}\n"
            f"    USING ({col} = current_setting('app.current_org', true)::uuid)\n"
            f"    WITH CHECK ({col} = current_setting('app.current_org', true)::uuid);"
        ),
    ]


def _disable_sql(table: str) -> list[str]:
    """DDL that removes the tenant-isolation policy from ``table``."""
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table};",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
    ]


def apply_rls(conn) -> None:
    """Enable tenant-isolation RLS on every protected table.

    ``conn`` is a synchronous SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
    From async code call ``await async_conn.run_sync(apply_rls)``; from an
    Alembic migration pass ``op.get_bind()``.
    """
    for table, col in _scoped_columns().items():
        for stmt in _enable_sql(table, col):
            conn.execute(sa.text(stmt))


def drop_rls(conn) -> None:
    """Remove tenant-isolation RLS from every protected table.

    Mirror of :func:`apply_rls`; see it for the ``conn`` contract.
    """
    for table in _scoped_columns():
        for stmt in _disable_sql(table):
            conn.execute(sa.text(stmt))
