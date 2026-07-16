"""
Integration tests for the multi-org bootcamp seed script.

Runs the seed against the test DB (fresh schema per test) and asserts:
- Org A "Career Forge": curriculum slug 'agentic-ai', 12 modules, 4
  projects, >=1 cohort under its active version.
- Org B "Acme Academy": curriculum slug 'cloud-data-eng', 4 modules, 2
  projects, >=1 cohort under its active version.
- The two orgs' data is DISJOINT: each curriculum carries its own
  organization_id and they differ (tenant isolation at the data layer).

Re-run idempotency: seed is called twice; counts must not double and the
second call must report skipped for both orgs.
"""

import pytest
from sqlalchemy import func, select

from app.core.cascade.engine import alignment_report_for_version
from app.core.diff.service import diff_versions
from app.core.workflow.engine import can_release
from app.models.cohort import Cohort
from app.models.curriculum import Curriculum
from app.models.graph import DependencyEdge
from app.models.org import Organization
from app.models.structure import Asset, AssetVersion, Module, Project
from app.models.version import Version
from app.models.workflow import Approval, ChangeRequest, QAReview
from app.models.enums import AssetKind, LifecycleStatus
from app.tenant import current_org, use_org

# Import seed function — uses the db_session passed in, not its own engine.
from seed.bootcamp_curriculum import (
    seed,
    CAREER_FORGE_CURRICULUM_SLUG,
    CAREER_FORGE_ORG_NAME,
    ACME_CURRICULUM_SLUG,
    ACME_ORG_NAME,
)


# The seed is an admin / cross-tenant provisioning path: in production it runs
# with NO ambient tenant context (``_main`` uses a fresh session), creating its
# OWN orgs and self-scoping via ``use_org``. The MT5 app-layer auto-filter
# (``app.tenant_scope``) keys off the ambient ``current_org`` — which the test
# fixture pins to DEFAULT_ORG. If we left that pin in place, the filter would
# (a) hide the seed-org rows from these read-backs and (b) make the seed's own
# idempotency guard ``select(Curriculum)`` miss the first run's row, breaking
# idempotency. Clearing the context reproduces production's unscoped seed path,
# so the filter is a no-op and reads see every seed org's data — which is the
# point: seeds run outside any single tenant's scope.
@pytest.fixture(autouse=True)
def _unscoped_seed_context(db_session):
    # Depend on db_session so this runs AFTER the fixture pins current_org to
    # DEFAULT_ORG — we then clear it for the duration of the seed test, and
    # restore on teardown (db_session's own teardown resets its token next).
    token = current_org.set(None)
    try:
        yield
    finally:
        current_org.reset(token)


async def _active_version_for_slug(db_session, slug: str) -> Version:
    curriculum = await db_session.scalar(
        select(Curriculum).where(Curriculum.slug == slug)
    )
    assert curriculum is not None, f"Curriculum '{slug}' not found"
    assert curriculum.current_version_id is not None, "current_version_id not set"
    active_version = await db_session.scalar(
        select(Version).where(
            Version.curriculum_id == curriculum.id,
            Version.status == LifecycleStatus.active,
        )
    )
    assert active_version is not None, f"No active version for '{slug}'"
    return curriculum, active_version


@pytest.mark.asyncio
async def test_seed_inserts_both_orgs(db_session):
    """Seed creates the IK (12-module) and Acme (4-module) curricula."""
    summary = await seed(db_session)
    assert not summary["skipped"], f"Seed was skipped unexpectedly: {summary}"

    # --- Org A: Career Forge ---
    ik_org = await db_session.scalar(
        select(Organization).where(Organization.name == CAREER_FORGE_ORG_NAME)
    )
    assert ik_org is not None, "IK organization not found"
    ik_curriculum, ik_active = await _active_version_for_slug(db_session, CAREER_FORGE_CURRICULUM_SLUG)
    assert ik_curriculum.name == "Agentic AI Architecture in Production"
    assert ik_curriculum.organization_id == ik_org.id
    assert ik_active.organization_id == ik_org.id

    ik_modules = await db_session.scalar(
        select(func.count(Module.id)).where(Module.version_id == ik_active.id)
    )
    assert ik_modules == 12, f"Expected 12 IK modules, got {ik_modules}"
    ik_projects = await db_session.scalar(
        select(func.count(Project.id)).where(Project.version_id == ik_active.id)
    )
    assert ik_projects == 4, f"Expected 4 IK projects, got {ik_projects}"
    ik_cohorts = await db_session.scalar(
        select(func.count(Cohort.id)).where(Cohort.curriculum_id == ik_curriculum.id)
    )
    assert ik_cohorts >= 1

    # --- Org B: Acme Academy ---
    acme_org = await db_session.scalar(
        select(Organization).where(Organization.name == ACME_ORG_NAME)
    )
    assert acme_org is not None, "Acme organization not found"
    acme_curriculum, acme_active = await _active_version_for_slug(db_session, ACME_CURRICULUM_SLUG)
    assert acme_curriculum.organization_id == acme_org.id
    assert acme_active.organization_id == acme_org.id

    acme_modules = await db_session.scalar(
        select(func.count(Module.id)).where(Module.version_id == acme_active.id)
    )
    assert acme_modules == 4, f"Expected 4 Acme modules, got {acme_modules}"
    acme_projects = await db_session.scalar(
        select(func.count(Project.id)).where(Project.version_id == acme_active.id)
    )
    assert acme_projects == 2, f"Expected 2 Acme projects, got {acme_projects}"
    acme_cohorts = await db_session.scalar(
        select(func.count(Cohort.id)).where(Cohort.curriculum_id == acme_curriculum.id)
    )
    assert acme_cohorts >= 1

    # --- Disjointness: the two orgs are distinct and own disjoint curricula ---
    assert ik_org.id != acme_org.id
    assert ik_curriculum.organization_id != acme_curriculum.organization_id


@pytest.mark.asyncio
async def test_seed_org_scoping_is_disjoint(db_session):
    """Under each org's tenant context, only that org's curriculum is visible."""
    await seed(db_session)

    ik_org = await db_session.scalar(
        select(Organization).where(Organization.name == CAREER_FORGE_ORG_NAME)
    )
    acme_org = await db_session.scalar(
        select(Organization).where(Organization.name == ACME_ORG_NAME)
    )

    # Scoped to IK: only the agentic-ai curriculum is visible (app-layer filter).
    with use_org(ik_org.id):
        ik_slugs = (
            await db_session.scalars(select(Curriculum.slug))
        ).all()
    assert set(ik_slugs) == {CAREER_FORGE_CURRICULUM_SLUG}, f"IK saw cross-tenant curricula: {ik_slugs}"

    # Scoped to Acme: only the cloud-data-eng curriculum is visible.
    with use_org(acme_org.id):
        acme_slugs = (
            await db_session.scalars(select(Curriculum.slug))
        ).all()
    assert set(acme_slugs) == {ACME_CURRICULUM_SLUG}, f"Acme saw cross-tenant curricula: {acme_slugs}"


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session):
    """Running seed twice does not duplicate records and reports skipped."""
    summary1 = await seed(db_session)
    assert not summary1["skipped"], "First seed run should not skip"

    summary2 = await seed(db_session)
    assert summary2["skipped"], "Second seed run must skip (idempotency)"
    for o in summary2["orgs"]:
        assert o["skipped"], f"Org not skipped on re-run: {o}"

    # Counts stay the same for IK.
    _, ik_active = await _active_version_for_slug(db_session, CAREER_FORGE_CURRICULUM_SLUG)
    ik_modules = await db_session.scalar(
        select(func.count(Module.id)).where(Module.version_id == ik_active.id)
    )
    assert ik_modules == 12, f"IK modules doubled after second seed: {ik_modules}"

    # Counts stay the same for Acme.
    _, acme_active = await _active_version_for_slug(db_session, ACME_CURRICULUM_SLUG)
    acme_modules = await db_session.scalar(
        select(func.count(Module.id)).where(Module.version_id == acme_active.id)
    )
    assert acme_modules == 4, f"Acme modules doubled after second seed: {acme_modules}"


# ---------------------------------------------------------------------------
# Demo-enrichment assertions (Part A): edges, asset-version history, CCRs.
# These guarantee the three headline features have data to display.
# ---------------------------------------------------------------------------


async def _asset_ids_for_version(db_session, version_id) -> list:
    mod_ids = (
        await db_session.scalars(select(Module.id).where(Module.version_id == version_id))
    ).all()
    proj_ids = (
        await db_session.scalars(select(Project.id).where(Project.version_id == version_id))
    ).all()
    assets = (
        await db_session.scalars(
            select(Asset).where(
                (Asset.module_id.in_(mod_ids)) | (Asset.project_id.in_(proj_ids))
            )
        )
    ).all()
    return [a.id for a in assets]


@pytest.mark.asyncio
async def test_seed_creates_dependency_edges(db_session):
    """Each curriculum gets a connected, multi-layer dependency DAG."""
    await seed(db_session)

    for slug in (CAREER_FORGE_CURRICULUM_SLUG, ACME_CURRICULUM_SLUG):
        _, active = await _active_version_for_slug(db_session, slug)
        asset_ids = await _asset_ids_for_version(db_session, active.id)
        edges = (
            await db_session.scalars(
                select(DependencyEdge).where(
                    DependencyEdge.from_asset_id.in_(asset_ids),
                    DependencyEdge.to_asset_id.in_(asset_ids),
                )
            )
        ).all()
        assert len(edges) > 0, f"{slug}: expected dependency edges, got none"

        # Depth check: the from→to spine must reach more than one BFS layer.
        adjacency: dict = {}
        for e in edges:
            adjacency.setdefault(e.from_asset_id, set()).add(e.to_asset_id)
        # Some asset has an outgoing edge whose target ALSO has an outgoing edge
        # (i.e. graph depth >= 2 — not a single flat row).
        has_depth = any(
            any(nxt in adjacency for nxt in targets) for targets in adjacency.values()
        )
        assert has_depth, f"{slug}: dependency graph has no depth (flat)"


@pytest.mark.asyncio
async def test_seed_creates_second_asset_versions_with_real_diffs(db_session):
    """A subset of assets has a 1.1.0 version whose diff returns real changes,
    covering all three differ families (rubric / learning_objectives / text)."""
    await seed(db_session)

    _, active = await _active_version_for_slug(db_session, CAREER_FORGE_CURRICULUM_SLUG)
    asset_ids = await _asset_ids_for_version(db_session, active.id)

    # Find assets that have >= 2 AssetVersions.
    multi = []
    for aid in asset_ids:
        avs = (
            await db_session.scalars(
                select(AssetVersion)
                .where(AssetVersion.asset_id == aid)
                .order_by(AssetVersion.created_at)
            )
        ).all()
        if len(avs) >= 2:
            multi.append((aid, avs))

    assert multi, "expected at least one asset with two versions"

    # Diff the oldest vs newest for each; verify non-empty diffs and collect kinds.
    families_seen = set()
    for aid, avs in multi:
        asset = await db_session.scalar(select(Asset).where(Asset.id == aid))
        result = await diff_versions(db_session, aid, avs[0].id, avs[-1].id)
        if result.structured is not None:
            changes = (
                len(result.structured.added)
                + len(result.structured.removed)
                + len(result.structured.changed)
            )
            assert changes > 0, f"asset {asset.kind} structured diff was empty"
            families_seen.add("structured:" + asset.kind.value)
        else:
            assert result.text is not None
            changes = len(result.text.added) + len(result.text.removed)
            assert changes > 0, f"asset {asset.kind} text diff was empty"
            families_seen.add("text")

    # All three diff styles are exercised by the seed.
    assert "structured:rubric" in families_seen, families_seen
    assert "structured:learning_objectives" in families_seen, families_seen
    assert "text" in families_seen, families_seen


@pytest.mark.asyncio
async def test_seed_creates_change_requests_with_qa_and_approvals(db_session):
    """Each org gets several CCRs across states, with QA reviews and the
    2-approver gate satisfied for approved/active CCRs."""
    await seed(db_session)

    for slug in (CAREER_FORGE_CURRICULUM_SLUG, ACME_CURRICULUM_SLUG):
        curriculum = await db_session.scalar(
            select(Curriculum).where(Curriculum.slug == slug)
        )
        ccrs = (
            await db_session.scalars(
                select(ChangeRequest).where(ChangeRequest.curriculum_id == curriculum.id)
            )
        ).all()
        assert len(ccrs) >= 5, f"{slug}: expected >=5 CCRs, got {len(ccrs)}"

        states = {c.status for c in ccrs}
        assert LifecycleStatus.draft in states, f"{slug}: no draft CCR"
        assert LifecycleStatus.review in states, f"{slug}: no review CCR"
        assert LifecycleStatus.approved in states, f"{slug}: no approved CCR"
        assert LifecycleStatus.active in states, f"{slug}: no active CCR"

        # Every CCR carries the required fields.
        for c in ccrs:
            assert c.title
            assert c.proposed_bump in ("major", "minor", "patch")
            assert c.target_version_id is not None
            assert c.impact is not None

        ccr_ids = [c.id for c in ccrs]

        # review+ CCRs each have a QAReview with all six canonical dimensions.
        from app.core.workflow.rules import QA_DIMENSIONS

        reviews = (
            await db_session.scalars(
                select(QAReview).where(QAReview.ccr_id.in_(ccr_ids))
            )
        ).all()
        assert reviews, f"{slug}: no QA reviews seeded"
        for r in reviews:
            assert set(QA_DIMENSIONS).issubset(r.dimension_scores.keys())
            assert r.verdict == "pass"

        # approved/active CCRs each have TWO approvals from DISTINCT approvers.
        gated = [
            c for c in ccrs
            if c.status in (LifecycleStatus.approved, LifecycleStatus.active)
        ]
        assert gated, f"{slug}: no gated (approved/active) CCRs"
        instructor_roles = {"instructor", "instructor_lead"}
        for c in gated:
            approvals = (
                await db_session.scalars(
                    select(Approval).where(Approval.ccr_id == c.id)
                )
            ).all()
            assert len(approvals) == 2, f"{slug}: CCR {c.title} needs 2 approvals"
            approver_ids = {a.approver_id for a in approvals}
            assert len(approver_ids) == 2, f"{slug}: approvals not from distinct approvers"
            # Release gate (can_release) requires >= 1 approval from an instructor
            # role — otherwise the seeded approved/active CCR can never be released.
            roles = {a.role for a in approvals}
            assert roles & instructor_roles, (
                f"{slug}: CCR {c.title} has no instructor-role approval "
                f"(release gate would reject it); roles={roles}"
            )
            # End-to-end: the REAL release gate must accept this seeded CCR.
            # (QA pass + >= 2 approvals + >= 1 instructor-role approval.)
            assert await can_release(db_session, c), (
                f"{slug}: seeded CCR {c.title} does NOT satisfy can_release "
                "— it could not be released at the demo's climax"
            )


@pytest.mark.asyncio
async def test_seed_creates_one_misalignment(db_session):
    """The graph surfaces a staleness flag: at least one stale dependent."""
    await seed(db_session)
    _, active = await _active_version_for_slug(db_session, CAREER_FORGE_CURRICULUM_SLUG)
    misalignments = await alignment_report_for_version(db_session, active.id)
    assert len(misalignments) >= 1, "expected at least one misaligned dependent"
