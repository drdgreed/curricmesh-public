"""Pure-assertion tests for the RLS DDL module (no DB required).

These guard the tenant-isolation contract and — critically — cross-check the
hand-maintained table list against the live ORM metadata so the two cannot
drift apart silently.
"""

from app.db.rls import (
    POLICY_NAME,
    TENANT_TABLES,
    _disable_sql,
    _enable_sql,
    _scoped_columns,
    drop_rls,
)

# The org-scoped tables: the 13 from the MT2 spec, version_pins (V3-B),
# sync_logs (V3-C), and the five immutable-version-model foundation tables.
_ORG_SCOPED_EXPECTED = {
    "curricula", "versions", "cohorts", "modules", "projects", "assets",
    "asset_versions", "change_requests", "qa_reviews", "approvals",
    "dependency_edges", "sota_findings", "history_events",
    "version_pins", "sync_logs",
    # Foundation: immutable, content-addressed version model.
    "lineage_assets", "content_versions", "curriculum_versions",
    "version_members", "version_edges",
    # Course Builder: mutable draft authoring model.
    "draft_courses", "draft_objectives", "draft_items",
    "draft_item_objectives", "draft_dependencies",
    "draft_rubric_results", "draft_advisor_notes",
    # Freshness pipeline (Phase 1): watchlist/snapshots/seen/runs.
    "source_watch_items", "syllabus_snapshots",
    "freshness_pipeline_seen", "freshness_pipeline_runs",
    # Judge (Phase 2)
    "gap_assessments",
    # Sync (Phase 4)
    "sync_targets",
    # Authoring media (slice 1)
    "media_assets",
    # Authoring media (slice 2): DraftItem <-> MediaAsset usage link.
    "draft_item_media",
    # Phase B (B2): per-asset extracted/transcribed text.
    "media_transcripts",
    # Learner delivery (Phase 2, Foundation 1).
    "enrollments", "learner_progress", "assessment_submissions",
    # Retrieval infra (Phase B, Foundation 1): version-pinned content chunks.
    "content_chunks",
    # Tutor (Phase B, B3): RAG Q&A conversation store (D5 server-side record).
    "tutor_conversations", "tutor_messages",
    # Async course generation: background-job tracking.
    "generation_jobs",
    # Slide System Port (S4): rendered deck ↔ CurriculumVersion linkage.
    "deck_artifacts",
}


def test_tenant_tables_membership():
    # Exactly the curriculum-domain tables — no more, no less.
    assert set(TENANT_TABLES) == _ORG_SCOPED_EXPECTED
    assert len(TENANT_TABLES) == 44
    # users / organizations are cross-tenant (login, provisioning) and
    # sota_sources is a global corpus — none may be tenant-scoped.
    assert "users" not in TENANT_TABLES
    assert "organizations" not in TENANT_TABLES
    assert "sota_sources" not in TENANT_TABLES


def test_scoped_columns_mapping():
    cols = _scoped_columns()
    # Every protected table is scoped on organization_id.
    assert set(cols) == _ORG_SCOPED_EXPECTED
    for table in _ORG_SCOPED_EXPECTED:
        assert cols[table] == "organization_id"


def test_enable_sql_policy_contents():
    stmts = _enable_sql("curricula", "organization_id")
    blob = "\n".join(stmts)
    assert "FORCE ROW LEVEL SECURITY" in blob
    assert f"CREATE POLICY {POLICY_NAME} ON curricula" in blob
    assert "current_setting('app.current_org', true)::uuid" in blob
    assert "USING (" in blob
    assert "WITH CHECK (" in blob


def test_disable_sql_contents():
    blob = "\n".join(_disable_sql("curricula"))
    assert f"DROP POLICY IF EXISTS {POLICY_NAME}" in blob
    assert "DISABLE ROW LEVEL SECURITY" in blob
    assert "NO FORCE ROW LEVEL SECURITY" in blob


def test_apply_and_drop_emit_all_statements():
    """apply_rls/drop_rls execute every statement for every protected table."""

    class _RecordingConn:
        def __init__(self):
            self.statements = []

        def execute(self, clause):
            self.statements.append(str(clause))

    from app.db.rls import apply_rls

    cols = _scoped_columns()

    apply_conn = _RecordingConn()
    apply_rls(apply_conn)
    assert len(apply_conn.statements) == sum(
        len(_enable_sql(t, c)) for t, c in cols.items()
    )

    drop_conn = _RecordingConn()
    drop_rls(drop_conn)
    assert len(drop_conn.statements) == sum(len(_disable_sql(t)) for t in cols)


def test_tenant_tables_cross_check_orm_metadata():
    """Every TENANT_TABLES name must be a real table; sota_sources excluded."""
    import app.models  # noqa: F401  (registers tables on Base.metadata)
    from app.database import Base

    registered = set(Base.metadata.tables)

    for table in TENANT_TABLES:
        assert table in registered, f"{table} is not a real ORM table"

    # sota_sources exists as a real table but is intentionally global.
    assert "sota_sources" in registered
    assert "sota_sources" not in TENANT_TABLES
