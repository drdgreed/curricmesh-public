"""
Seed script: multi-org CurricMesh demo (proves tenant isolation).

SYNTHETIC / DEMO DATA ONLY — not real course content.

Creates TWO organizations so the demo visibly shows multi-tenant isolation:

  * Org A — "Career Forge": the full 12-week "Agentic AI
    Architecture in Production" bootcamp + 6 role users (<role>@careerforge.demo).
  * Org B — "Acme Academy": a LIGHTWEIGHT second org — 6 role users
    (<role>@acme.demo) + one small 4-module curriculum + a cohort. Just
    enough to be visibly distinct from Career Forge on the dashboard.

Logging in as each org's users shows ONLY that org's data — the seed
write-stamps every domain row with its org via the tenant context.

Tenant scoping
--------------
``Organization`` rows are NOT tenant-scoped (they are structural identity
rows — see AGENT_LESSONS P-005), so they are created OUTSIDE any tenant
context. Each org's users + curriculum are then created inside
``with use_org(org.id):`` so every domain row stamps that org's id and the
per-org idempotency guard (``select(Curriculum)`` is auto-filtered to the
active org by the MT5 app-layer filter) only sees that org's rows.

``_main`` runs with NO ambient tenant context; the seed sets context per
org via ``use_org``.

Idempotent: safe to re-run. Each org is guarded independently by its
curriculum slug, so a partial run (org A done, org B not) self-heals on
re-run.

Usage:
    cd backend
    ./venv/bin/python -m seed.bootcamp_curriculum        # via module
    ./venv/bin/python seed/bootcamp_curriculum.py        # direct

Demo password: demo-pass-123  (same for every seeded user).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure app package is importable when run as a script from outside the backend dir.
import os

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.auth.passwords import hash_password
from app.config import settings
from app.database import Base, bind_session_org
from app.models import (  # noqa: F401 — registers all tables on Base.metadata
    Approval,
    AssetKind,
    AssetVersion,
    Asset,
    Cohort,
    ChangeRequest,
    Curriculum,
    DependencyEdge,
    HistoryEvent,
    LifecycleStatus,
    Module,
    Organization,
    Project,
    QAReview,
    User,
    Version,
)
from app.core.history import EventType
from app.migration.backfill_content_model import backfill_content_model
from app.tenant import use_org


@asynccontextmanager
async def _bound_org(session: AsyncSession, org_id: uuid.UUID):
    """Enter the tenant context for *org_id* on BOTH layers, together.

    ``use_org`` sets the app-layer ContextVar (write-stamp + auto-filter);
    ``bind_session_org`` pushes the same org to the DB GUC ``app.current_org``
    so Postgres RLS admits the writes. The seed runs against a production-shaped
    DB where ``FORCE ROW LEVEL SECURITY`` is on and the connecting role does NOT
    bypass RLS, so the GUC is mandatory — the ContextVar alone leaves the GUC
    unset and every org-scoped INSERT is rejected (``new row violates
    row-level security policy``).

    The GUC is session-scoped (see ``bind_session_org``), so it survives the
    flushes and mid-block commits inside a tenant block and is refreshed here at
    the start of the next block — which is what makes the multi-org, single-
    transaction switch in ``seed()`` correct: each org's rows flush while the GUC
    matches that org, then the next ``_bound_org`` re-points it.

    Use this in place of a bare ``with use_org(...)`` at every seed tenant block
    so the two layers can never drift.
    """
    with use_org(org_id):
        await bind_session_org(session, org_id)
        yield

# ---------------------------------------------------------------------------
# Demo password — document it clearly.
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "demo-pass-123"

# Roles seeded for every org (same six everywhere).
_ROLES = [
    ("architect", "Architect"),
    ("program_manager", "Program-Manager"),
    ("instructor_lead", "Instructor-Lead"),
    ("instructor", "Instructor"),
    ("qa_lead", "QA-Lead"),
    ("devops", "DevOps"),
]


def _users_for(domain: str, first_names: dict[str, str]) -> list[dict]:
    """Build the six role-user records for an org's email *domain* (e.g. 'careerforge.demo')."""
    out = []
    for role, label in _ROLES:
        first = first_names[role]
        out.append(
            {
                "email": f"{role}@{domain}",
                "display_name": f"{first} {label}",
                "role": role,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Org A — "Career Forge" (full 12-week bootcamp)
# ---------------------------------------------------------------------------

CAREER_FORGE_ORG_NAME = "Career Forge"
CAREER_FORGE_EMAIL_DOMAIN = "careerforge.demo"
CAREER_FORGE_USERS = _users_for(
    CAREER_FORGE_EMAIL_DOMAIN,
    {
        "architect": "Alex",
        "program_manager": "Pat",
        "instructor_lead": "Iris",
        "instructor": "Ivan",
        "qa_lead": "Quinn",
        "devops": "Devon",
    },
)

CAREER_FORGE_CURRICULUM_NAME = "Agentic AI Architecture in Production"
CAREER_FORGE_CURRICULUM_SLUG = "agentic-ai"

# A learner-role user for the Course Player demo. Not one of the six author
# roles — created (and enrolled) CLI-only via ``_demo_learner_enroll`` so the
# test suite (which seeds via ``seed()``) is entirely unaffected.
CAREER_FORGE_LEARNER = {
    "email": "learner@careerforge.demo",
    "display_name": "Lee Learner",
    "role": "learner",
}

# 12 modules (weeks)
CAREER_FORGE_MODULES = [
    "Foundations: LLMs in Production Systems",
    "Prompt Engineering & Chain-of-Thought",
    "Retrieval-Augmented Generation (RAG)",
    "Vector Databases & Embedding Strategies",
    "Agent Architectures: ReAct, Plan-and-Execute",
    "Tool Use & Function Calling",
    "Multi-Agent Orchestration",
    "Memory Systems & Long-Context Management",
    "Evaluation & Red-Teaming Agentic Systems",
    "Safety, Alignment & Guardrails",
    "Deployment: Latency, Cost & Reliability",
    "Capstone: Full-Stack Agentic Application",
]

# 4 projects
CAREER_FORGE_PROJECTS = [
    "RAG-Powered Technical Interview Coach",
    "Multi-Agent Code Review Pipeline",
    "Autonomous Research Summarizer",
    "Production Agentic System (Capstone)",
]

# ---------------------------------------------------------------------------
# Org B — "Acme Academy" (lightweight)
# ---------------------------------------------------------------------------

ACME_ORG_NAME = "Acme Academy"
ACME_EMAIL_DOMAIN = "acme.demo"
ACME_USERS = _users_for(
    ACME_EMAIL_DOMAIN,
    {
        "architect": "Avery",
        "program_manager": "Morgan",
        "instructor_lead": "Lena",
        "instructor": "Theo",
        "qa_lead": "Quincy",
        "devops": "Dana",
    },
)

ACME_CURRICULUM_NAME = "Cloud Data Engineering Essentials"
ACME_CURRICULUM_SLUG = "cloud-data-eng"

# 4 modules — small, visibly distinct from Career Forge's 12-week build.
ACME_MODULES = [
    "Data Warehousing Fundamentals",
    "Batch & Stream Pipelines",
    "dbt & Analytics Engineering",
    "Capstone: End-to-End Data Platform",
]

ACME_PROJECTS = [
    "Streaming ETL Pipeline",
    "Analytics Warehouse Capstone",
]

# Asset kinds per module: (kind, status)
# Mix of active, review, draft to make the dashboard interesting.
MODULE_ASSET_PATTERNS = [
    # index 0: rich — lesson_plan + slides + learning_objectives, all active
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.slides, LifecycleStatus.active),
        (AssetKind.learning_objectives, LifecycleStatus.active),
    ],
    # index 1
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.slides, LifecycleStatus.review),
        (AssetKind.assessment, LifecycleStatus.active),
    ],
    # index 2
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.slides, LifecycleStatus.active),
        (AssetKind.learning_objectives, LifecycleStatus.draft),
    ],
    # index 3
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.slides, LifecycleStatus.active),
        (AssetKind.assessment, LifecycleStatus.review),
    ],
    # Repeat pattern with minor variation for remaining modules.
]

# For modules with index >= len(MODULE_ASSET_PATTERNS), cycle through the first 2 patterns.
_FALLBACK_ASSET_PATTERNS = [
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.slides, LifecycleStatus.active),
    ],
    [
        (AssetKind.lesson_plan, LifecycleStatus.active),
        (AssetKind.assessment, LifecycleStatus.active),
        (AssetKind.learning_objectives, LifecycleStatus.active),
    ],
]

PROJECT_ASSET_PATTERNS = [
    # rubric + spec, all active
    [(AssetKind.rubric, LifecycleStatus.active), (AssetKind.spec, LifecycleStatus.active)],
    # rubric active, spec in review
    [(AssetKind.rubric, LifecycleStatus.active), (AssetKind.spec, LifecycleStatus.review)],
    # rubric active, starter draft
    [(AssetKind.rubric, LifecycleStatus.active), (AssetKind.starter, LifecycleStatus.draft)],
    # rubric + spec + starter all active (capstone, richest)
    [
        (AssetKind.rubric, LifecycleStatus.active),
        (AssetKind.spec, LifecycleStatus.active),
        (AssetKind.starter, LifecycleStatus.active),
    ],
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_assets(module_index: int) -> list[tuple[AssetKind, LifecycleStatus]]:
    if module_index < len(MODULE_ASSET_PATTERNS):
        return MODULE_ASSET_PATTERNS[module_index]
    return _FALLBACK_ASSET_PATTERNS[module_index % len(_FALLBACK_ASSET_PATTERNS)]


def _project_assets(project_index: int) -> list[tuple[AssetKind, LifecycleStatus]]:
    return PROJECT_ASSET_PATTERNS[project_index % len(PROJECT_ASSET_PATTERNS)]


# ---------------------------------------------------------------------------
# Diffable body content
# ---------------------------------------------------------------------------
# IMPORTANT: ``diff_versions`` (app/core/diff/service.py) diffs the ``body_ref``
# field — NOT ``metadata_``. The dispatch by kind is:
#   * rubric              → JSON {"criteria": [{"name", "weight"}, ...]} via rubric_diff
#   * learning_objectives → JSON [{"id", "text"}, ...]                   via lo_diff
#   * lesson_plan/slides/spec/lab/references/starter → plain text        via text_diff
#   * assessment          → falls through to text_diff (no structured differ)
# So we must put real, diffable content in body_ref (the gs:// path is useless
# for the diff). We seed v1.0.0 bodies here; ``_changed_body`` produces the
# genuinely-different v1.1.0 body for the kinds we give a second version.

def _body_label(av: "AssetVersion") -> str:
    """Recover the human label used to build an AssetVersion's body (for the 1.1.0 body)."""
    meta = av.metadata_ or {}
    base = meta.get("label") or meta.get("kind") or "asset"
    return f"{base} — {meta.get('kind', '')}".strip(" —")


def _initial_body(kind: AssetKind, label: str) -> str:
    """Return diffable v1.0.0 ``body_ref`` content for *kind* (JSON for structured)."""
    if kind == AssetKind.rubric:
        return json.dumps(
            {
                "criteria": [
                    {"name": "Correctness", "weight": 0.40},
                    {"name": "Code Quality", "weight": 0.25},
                    {"name": "Documentation", "weight": 0.20},
                    {"name": "Testing", "weight": 0.15},
                ]
            }
        )
    if kind == AssetKind.learning_objectives:
        return json.dumps(
            [
                {"id": "lo1", "text": f"Explain the core concepts of {label}."},
                {"id": "lo2", "text": f"Apply {label} techniques to a guided exercise."},
                {"id": "lo3", "text": f"Evaluate trade-offs when using {label} in production."},
            ]
        )
    # Text-ish kinds (and assessment): markdown body.
    return (
        f"# {label}\n"
        f"\n"
        f"## Overview\n"
        f"This {kind.value} covers the fundamentals of {label}.\n"
        f"\n"
        f"## Outline\n"
        f"- Motivation and context\n"
        f"- Core techniques\n"
        f"- Hands-on walkthrough\n"
        f"- Common pitfalls\n"
    )


def _changed_body(kind: AssetKind, label: str) -> str:
    """Return a genuinely-different v1.1.0 ``body_ref`` so the diff is non-empty.

    Each kind produces a change matching what its differ detects:
      * rubric              → a changed weight + an added criterion (changed + added)
      * learning_objectives → an added LO + an edited LO text     (added + changed)
      * text/assessment     → edited + added lines                (added + removed)
    """
    if kind == AssetKind.rubric:
        return json.dumps(
            {
                "criteria": [
                    {"name": "Correctness", "weight": 0.35},  # changed 0.40 -> 0.35
                    {"name": "Code Quality", "weight": 0.25},
                    {"name": "Documentation", "weight": 0.15},  # changed 0.20 -> 0.15
                    {"name": "Testing", "weight": 0.15},
                    {"name": "Reproducibility", "weight": 0.10},  # added
                ]
            }
        )
    if kind == AssetKind.learning_objectives:
        return json.dumps(
            [
                {"id": "lo1", "text": f"Explain the core concepts of {label}."},
                # lo2 text edited (changed):
                {"id": "lo2", "text": f"Apply {label} techniques to an unguided, real-world task."},
                {"id": "lo3", "text": f"Evaluate trade-offs when using {label} in production."},
                # lo4 added:
                {"id": "lo4", "text": f"Critique a peer's {label} implementation against the rubric."},
            ]
        )
    return (
        f"# {label}\n"
        f"\n"
        f"## Overview\n"
        f"This {kind.value} covers the fundamentals and advanced applications of {label}.\n"  # edited
        f"\n"
        f"## Outline\n"
        f"- Motivation and context\n"
        f"- Core techniques\n"
        f"- Advanced patterns\n"  # added
        f"- Hands-on walkthrough\n"
        f"- Common pitfalls and how to debug them\n"  # edited
        f"- Further reading\n"  # added
    )


async def _seed_users(session: AsyncSession, org: Organization, users: list[dict]) -> dict[str, User]:
    """Create the role users for *org* (assumes we are inside that org's context)."""
    pw_hash = hash_password(DEMO_PASSWORD)
    records: dict[str, User] = {}
    for ud in users:
        user = User(
            email=ud["email"],
            display_name=ud["display_name"],
            role=ud["role"],
            password_hash=pw_hash,
            organization_id=org.id,
        )
        session.add(user)
        records[ud["role"]] = user
    await session.flush()
    return records


async def _seed_curriculum(
    session: AsyncSession,
    org: Organization,
    *,
    name: str,
    slug: str,
    modules: list[str],
    projects: list[str],
    cohort_name: str,
    cohort_label: str,
    users: dict[str, User],
    active_notes: str,
) -> dict:
    """Create a full curriculum (versions, modules, projects, assets, cohort, history).

    Runs inside the org's tenant context (caller wraps in ``use_org``). Returns a
    summary dict. This single builder serves both the full Career Forge bootcamp and the
    lightweight Acme curriculum — the only difference is the module/project lists.
    """
    # --- Curriculum ---
    curriculum = Curriculum(name=name, slug=slug, current_version_id=None)
    session.add(curriculum)
    await session.flush()

    # --- Version 0.9.0 (archived — history flavor) ---
    version_archived = Version(
        curriculum_id=curriculum.id,
        major=0,
        minor=9,
        patch=0,
        status=LifecycleStatus.archived,
        notes="Initial pilot; replaced by 1.0.0 after feedback.",
    )
    session.add(version_archived)
    await session.flush()

    # --- Version 1.0.0 (active — the live version) ---
    version_active = Version(
        curriculum_id=curriculum.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
        notes=active_notes,
    )
    session.add(version_active)
    await session.flush()

    curriculum.current_version_id = version_active.id
    await session.flush()

    # --- Modules ---
    module_records: list[Module] = []
    for i, focus in enumerate(modules):
        mod = Module(version_id=version_active.id, index=i + 1, focus=focus)
        session.add(mod)
        module_records.append(mod)
    await session.flush()

    # --- Projects ---
    project_records: list[Project] = []
    for i, title in enumerate(projects):
        proj = Project(version_id=version_active.id, index=i + 1, title=title)
        session.add(proj)
        project_records.append(proj)
    await session.flush()

    # --- Assets + AssetVersions for modules ---
    # We keep flat records so we can wire a layered dependency DAG and add
    # second versions afterwards. ``module_assets[i]`` holds the assets of the
    # i-th module (in week order) so we can chain earlier weeks → later weeks.
    asset_count = 0
    module_assets: list[list[Asset]] = []
    # ``initial_av`` maps asset.id → its v1.0.0 AssetVersion (used for misalignment
    # back-dating and as the diff "from" version).
    initial_av: dict = {}
    # ``base_created_at`` is a deterministic, monotonically-increasing clock so
    # later-week assets read as "more recently changed" than earlier weeks. This
    # is what lets us deterministically create ONE misaligned dependent.
    base_created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = [0]  # mutable counter captured by the helper below

    def _next_ts() -> datetime:
        clock[0] += 1
        return base_created_at + timedelta(hours=clock[0])

    for mod_idx, mod in enumerate(module_records):
        this_module: list[Asset] = []
        for kind, status in _module_assets(mod_idx):
            asset = Asset(
                kind=kind,
                key=f"{slug}/v1/{mod.index:02d}/{kind.value}",
                module_id=mod.id,
                project_id=None,
            )
            session.add(asset)
            await session.flush()
            av = AssetVersion(
                asset_id=asset.id,
                major=1,
                minor=0,
                patch=0,
                status=status,
                body_ref=_initial_body(kind, f"{mod.focus} — {kind.value}"),
                metadata_={"week": mod.index, "kind": kind.value, "label": str(mod.focus)},
                created_at=_next_ts(),
            )
            session.add(av)
            initial_av[asset.id] = av
            this_module.append(asset)
            asset_count += 1
        module_assets.append(this_module)
    await session.flush()

    # --- Assets + AssetVersions for projects ---
    project_assets: list[list[Asset]] = []
    for proj_idx, proj in enumerate(project_records):
        this_project: list[Asset] = []
        for kind, status in _project_assets(proj_idx):
            asset = Asset(
                kind=kind,
                key=f"{slug}/v1/projects/{proj.index:02d}/{kind.value}",
                module_id=None,
                project_id=proj.id,
            )
            session.add(asset)
            await session.flush()
            av = AssetVersion(
                asset_id=asset.id,
                major=1,
                minor=0,
                patch=0,
                status=status,
                body_ref=_initial_body(kind, f"{proj.title} — {kind.value}"),
                metadata_={"project": proj.index, "kind": kind.value, "label": proj.title},
                created_at=_next_ts(),
            )
            session.add(av)
            initial_av[asset.id] = av
            this_project.append(asset)
            asset_count += 1
        project_assets.append(this_project)
    await session.flush()

    # --- Dependency edges → a layered DAG (the graph + cascade) ---
    # Edge direction (verified in app/core/cascade/engine.py): from = upstream
    # prerequisite, to = downstream dependent; a change to `from` cascades to `to`.
    # We build:
    #   (a) week N's lesson_plan → week N+1's lesson_plan  (the spine: depth = #modules)
    #   (b) each module's lesson_plan → its own siblings    (intra-week support)
    #   (c) the final module's lesson_plan → each project's primary asset
    #       and project[i] → project[i+1]                   (module → project deps)
    # This yields a connected, multi-layer graph (~1–2 outgoing edges/asset avg),
    # not a hairball and not a single chain.
    edge_specs: list[tuple[Asset, Asset, str]] = []

    def _lesson_or_first(assets: list[Asset]) -> Asset:
        for a in assets:
            if a.kind == AssetKind.lesson_plan:
                return a
        return assets[0]

    # (b) intra-week: lesson_plan supports its siblings
    for assets in module_assets:
        if not assets:
            continue
        anchor = _lesson_or_first(assets)
        for sib in assets:
            if sib.id != anchor.id:
                edge_specs.append((anchor, sib, "supports"))

    # (a) week spine: week N lesson_plan → week N+1 lesson_plan
    anchors = [_lesson_or_first(a) for a in module_assets if a]
    for upstream, downstream in zip(anchors, anchors[1:]):
        edge_specs.append((upstream, downstream, "prerequisite"))

    # (c) module → project: final module anchor → each project's primary asset,
    #     then project[i] → project[i+1].
    if anchors and project_assets:
        final_anchor = anchors[-1]
        project_anchors = [pa[0] for pa in project_assets if pa]
        for panchor in project_anchors:
            edge_specs.append((final_anchor, panchor, "prerequisite"))
        for upstream, downstream in zip(project_anchors, project_anchors[1:]):
            edge_specs.append((upstream, downstream, "prerequisite"))

    edge_count = 0
    seen_edges: set[tuple] = set()
    for upstream, downstream, etype in edge_specs:
        key = (upstream.id, downstream.id, etype)
        if upstream.id == downstream.id or key in seen_edges:
            continue
        seen_edges.add(key)
        session.add(
            DependencyEdge(
                from_asset_id=upstream.id,
                to_asset_id=downstream.id,
                edge_type=etype,
            )
        )
        edge_count += 1
    await session.flush()

    # --- Second AssetVersion (1.1.0) for a diffable subset (the diff page) ---
    # Pick assets across the THREE differ families so the diff page shows all
    # three styles: rubric (structured weights), learning_objectives (structured
    # id/text), and a text kind (lesson_plan/slides/spec). We bump minor and set
    # body_ref to genuinely-different content via _changed_body.
    second_version_count = 0
    wanted_kinds = {
        AssetKind.rubric,
        AssetKind.learning_objectives,
        AssetKind.lesson_plan,
        AssetKind.slides,
        AssetKind.spec,
    }
    # Deterministic order: modules first, then projects.
    flat_assets: list[Asset] = [a for grp in module_assets for a in grp]
    flat_assets += [a for grp in project_assets for a in grp]
    # Cap at 10; ensure we cover at least rubric + LO + a text kind.
    seen_kinds: set[AssetKind] = set()
    for asset in flat_assets:
        if second_version_count >= 10:
            break
        if asset.kind not in wanted_kinds:
            continue
        # Prefer breadth across kinds first, then fill up to the cap.
        if asset.kind in seen_kinds and second_version_count >= 6:
            continue
        seen_kinds.add(asset.kind)
        # Reuse the same human label used for the initial body so diffs read sensibly.
        first = initial_av[asset.id]
        new_status = (
            LifecycleStatus.active
            if first.status == LifecycleStatus.active
            else LifecycleStatus.approved
        )
        av2 = AssetVersion(
            asset_id=asset.id,
            major=1,
            minor=1,
            patch=0,
            status=new_status,
            body_ref=_changed_body(asset.kind, _body_label(first)),
            metadata_={**(first.metadata_ or {}), "revision": "1.1.0"},
            created_at=_next_ts(),
        )
        session.add(av2)
        second_version_count += 1
    await session.flush()

    # --- (Nice-to-have) one deterministic misalignment ---
    # Make an early-week dependent asset's latest version OLDER than its upstream
    # prerequisite so alignment_report flags it (red staleness border). We pick the
    # week-1 → week-2 spine edge: back-date week-2's anchor latest version to BEFORE
    # week-1's. We do this by inserting a fresh v1.1.0 for week-1's anchor with a
    # very recent timestamp, while week-2's anchor keeps its old v1.0.0 timestamp.
    misaligned_count = 0
    if len(anchors) >= 2:
        upstream_anchor = anchors[0]   # week 1
        downstream_anchor = anchors[1]  # week 2 (already has only an old v1.0.0)
        # Only create the bump if week-2's anchor did NOT already get a 2nd version
        # above (keep its latest timestamp old). If it did, skip — staleness would
        # be masked. In practice week-2 anchor is a lesson_plan and may have been
        # picked; guard by checking we won't double-bump the upstream.
        up_first = initial_av[upstream_anchor.id]
        bump = AssetVersion(
            asset_id=upstream_anchor.id,
            major=1,
            minor=2,
            patch=0,
            status=up_first.status,
            body_ref=_changed_body(upstream_anchor.kind, _body_label(up_first)),
            metadata_={**(up_first.metadata_ or {}), "revision": "1.2.0-restale"},
            created_at=_next_ts(),  # newest of all → downstream now predates it
        )
        session.add(bump)
        misaligned_count = 1
    await session.flush()

    # --- ChangeRequests across the lifecycle + QA + approvals (the workflow) ---
    # 6 CCRs spanning draft / review / approved / active. The release gate
    # (app/core/workflow/engine.py::can_release) requires, for approved/active
    # CCRs: a passing QAReview, >= 2 Approval rows from DISTINCT approvers, AND
    # >= 1 approval from an instructor role ({instructor, instructor_lead}). So
    # approved/active CCRs carry TWO Approval rows (architect + instructor_lead)
    # and a QAReview with the six canonical dimension scores (QA_DIMENSIONS in
    # app/core/workflow/rules.py).
    architect = users["architect"]
    pm = users["program_manager"]
    qa = users["qa_lead"]
    instructor_lead = users["instructor_lead"]

    def _qa_scores() -> dict:
        return {
            "content_accuracy": 5,
            "alignment": 4,
            "prerequisites": 4,
            "consistency": 5,
            "instructor_support": 4,
            "student_experience": 5,
        }

    # (status, title, rationale, bump, impact)
    ccr_specs = [
        (
            LifecycleStatus.draft,
            "Add reproducibility criterion to capstone rubric",
            "Graders want explicit credit for reproducible builds; aligns rubric with industry expectations.",
            "minor",
            {"affected_kinds": ["rubric"], "estimated_assets": 1},
        ),
        (
            LifecycleStatus.draft,
            "Clarify week-1 learning objectives wording",
            "Pilot feedback: lo2 phrasing implies a guided task; reword for an unguided, real-world task.",
            "patch",
            {"affected_kinds": ["learning_objectives"], "estimated_assets": 1},
        ),
        (
            LifecycleStatus.review,
            "Expand lesson plans with advanced patterns section",
            "Add an 'Advanced patterns' section + debugging guidance across core lessons.",
            "minor",
            {"affected_kinds": ["lesson_plan", "slides"], "estimated_assets": 4},
        ),
        (
            LifecycleStatus.review,
            "Tighten assessment alignment to objectives",
            "QA flagged drift between assessments and stated objectives; realign before next cohort.",
            "minor",
            {"affected_kinds": ["assessment", "learning_objectives"], "estimated_assets": 3},
        ),
        (
            LifecycleStatus.approved,
            "Rebalance rubric weights toward correctness",
            "Reduce Documentation weight, introduce Reproducibility; ratified by architecture review.",
            "minor",
            {"affected_kinds": ["rubric"], "estimated_assets": 2},
        ),
        (
            LifecycleStatus.active,
            "Ship v1.1 content refresh across early modules",
            "Merged refresh: edited overviews, new further-reading, expanded objectives. "
            "A minor bump while a cohort is active normally violates the patch-only "
            "mid-cohort rule (assert_patch_only_mid_cohort), so the instructor lead "
            "applied an explicit instructor_override to ship it to the live cohort.",
            "minor",
            {
                "affected_kinds": ["lesson_plan", "learning_objectives", "rubric"],
                "estimated_assets": 6,
                "instructor_override": True,
                "override_note": "Instructor override approved: minor refresh shipped mid-cohort.",
            },
        ),
    ]

    ccr_count = qa_review_count = approval_count = 0
    for status, title, rationale, bump_str, impact in ccr_specs:
        author = architect if status in (LifecycleStatus.approved, LifecycleStatus.active) else pm
        ccr = ChangeRequest(
            curriculum_id=curriculum.id,
            author_id=author.id,
            target_version_id=version_active.id,
            title=title,
            rationale=rationale,
            proposed_bump=bump_str,
            impact=impact,
            status=status,
        )
        session.add(ccr)
        await session.flush()
        ccr_count += 1

        # QA review for review+ states.
        if status in (LifecycleStatus.review, LifecycleStatus.approved, LifecycleStatus.active):
            session.add(
                QAReview(
                    ccr_id=ccr.id,
                    reviewer_id=qa.id,
                    dimension_scores=_qa_scores(),
                    verdict="pass",
                )
            )
            qa_review_count += 1

        # Two approvals from DISTINCT approvers for approved/active. The release
        # gate (app/core/workflow/engine.py::can_release) requires >= 2 approvals
        # AND >= 1 from an instructor role ({instructor, instructor_lead}), so one
        # approver MUST be the instructor_lead — otherwise the seeded approved/active
        # CCRs could never be released (400 at the demo's climax).
        if status in (LifecycleStatus.approved, LifecycleStatus.active):
            for approver, role in (
                (architect, "architect"),
                (instructor_lead, "instructor_lead"),
            ):
                session.add(
                    Approval(
                        ccr_id=ccr.id,
                        approver_id=approver.id,
                        role=role,
                        decision="approve",
                    )
                )
                approval_count += 1
    await session.flush()

    # --- Cohort (active: spans today) ---
    today = date.today()
    cohort = Cohort(
        curriculum_id=curriculum.id,
        version_id=version_active.id,
        name=cohort_name,
        start_date=today - timedelta(weeks=4),
        end_date=today + timedelta(weeks=8),
        instructors=[
            str(users["instructor_lead"].id),
            str(users["instructor"].id),
        ],
    )
    session.add(cohort)
    await session.flush()

    # --- History events ---
    history_rows = [
        HistoryEvent(
            actor_id=users["instructor_lead"].id,
            event_type=EventType.ccr_created,
            target=f"curriculum:{curriculum.id}",
            details={"version": "0.9.0", "note": "Initial CCR to draft 0.9.0 structure"},
        ),
        HistoryEvent(
            actor_id=users["qa_lead"].id,
            event_type=EventType.version_approved,
            target=f"version:{version_archived.id}",
            details={"version": "0.9.0", "note": "QA pass — pilot approved for archived record"},
        ),
        HistoryEvent(
            actor_id=users["program_manager"].id,
            event_type=EventType.version_active,
            target=f"version:{version_active.id}",
            details={"version": "1.0.0", "note": f"{cohort_label} released v1.0.0 to active"},
        ),
        HistoryEvent(
            actor_id=users["qa_lead"].id,
            event_type=EventType.qa_passed,
            target=f"curriculum:{curriculum.id}",
            details={"note": "Six-dimension QA passed for v1.0.0 content review"},
        ),
    ]
    for evt in history_rows:
        session.add(evt)
    await session.flush()

    return {
        "curriculum": name,
        "curriculum_slug": slug,
        "curriculum_id": str(curriculum.id),
        "versions": {
            "0.9.0 (archived)": str(version_archived.id),
            "1.0.0 (active)": str(version_active.id),
        },
        "modules": len(module_records),
        "projects": len(project_records),
        "assets": asset_count,
        "dependency_edges": edge_count,
        "second_versions": second_version_count,
        "misaligned": misaligned_count,
        "change_requests": ccr_count,
        "qa_reviews": qa_review_count,
        "approvals": approval_count,
        "cohorts": 1,
        "history_events": len(history_rows),
    }


# ---------------------------------------------------------------------------
# Per-org seed (org row outside context, domain inside use_org)
# ---------------------------------------------------------------------------

async def _seed_org(
    session: AsyncSession,
    *,
    org_name: str,
    users: list[dict],
    curriculum_slug: str,
    seed_curriculum_kwargs: dict,
) -> dict:
    """Get-or-create *org_name* and seed its users + curriculum.

    The ``Organization`` row is created OUTSIDE any tenant context (organizations
    is not tenant-scoped). All domain rows (users + curriculum) are created INSIDE
    ``use_org(org.id)`` so every row write-stamps this org and the per-org
    idempotency guard only sees this org's curricula.
    """
    # Find an existing org by name (unscoped — organizations is not RLS-scoped).
    org = await session.scalar(select(Organization).where(Organization.name == org_name))
    created_org = False
    if org is None:
        org = Organization(name=org_name)
        session.add(org)
        await session.flush()
        created_org = True

    async with _bound_org(session, org.id):
        # Per-org idempotency: this select is auto-filtered to org by the MT5
        # app-layer filter, so it only matches THIS org's curriculum.
        existing = await session.scalar(
            select(Curriculum).where(Curriculum.slug == curriculum_slug)
        )
        if existing is not None:
            return {
                "skipped": True,
                "organization": org_name,
                "organization_id": str(org.id),
                "curriculum_slug": curriculum_slug,
                "curriculum_id": str(existing.id),
            }

        user_records = await _seed_users(session, org, users)
        summary = await _seed_curriculum(
            session, org, users=user_records, **seed_curriculum_kwargs
        )

    summary.update(
        {
            "skipped": False,
            "organization": org_name,
            "organization_id": str(org.id),
            "created_org": created_org,
            "users": len(users),
            "email_domain": users[0]["email"].split("@", 1)[1] if users else "",
        }
    )
    return summary


# ---------------------------------------------------------------------------
# Main seed function — orchestrates both orgs
# ---------------------------------------------------------------------------

async def seed(session: AsyncSession) -> dict:
    """Seed both demo orgs (Career Forge full + Acme lightweight); return a summary dict.

    Must be called with NO ambient tenant context — each org self-scopes via
    ``use_org``. Idempotent per org.
    """
    today = date.today()

    org_a = await _seed_org(
        session,
        org_name=CAREER_FORGE_ORG_NAME,
        users=CAREER_FORGE_USERS,
        curriculum_slug=CAREER_FORGE_CURRICULUM_SLUG,
        seed_curriculum_kwargs=dict(
            name=CAREER_FORGE_CURRICULUM_NAME,
            slug=CAREER_FORGE_CURRICULUM_SLUG,
            modules=CAREER_FORGE_MODULES,
            projects=CAREER_FORGE_PROJECTS,
            cohort_name="Career Forge Cohort 2026-Q2",
            cohort_label="Career Forge program manager",
            active_notes="Production version launched after Career Forge bootcamp review.",
        ),
    )

    org_b = await _seed_org(
        session,
        org_name=ACME_ORG_NAME,
        users=ACME_USERS,
        curriculum_slug=ACME_CURRICULUM_SLUG,
        seed_curriculum_kwargs=dict(
            name=ACME_CURRICULUM_NAME,
            slug=ACME_CURRICULUM_SLUG,
            modules=ACME_MODULES,
            projects=ACME_PROJECTS,
            cohort_name="Acme Cohort 2026-Spring",
            cohort_label="Acme program manager",
            active_notes="First Acme Academy release of the data-engineering track.",
        ),
    )

    await session.commit()

    skipped = org_a.get("skipped") and org_b.get("skipped")
    return {
        "skipped": skipped,
        "demo_password": DEMO_PASSWORD,
        "orgs": [org_a, org_b],
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _print_org(o: dict) -> None:
    if o.get("skipped"):
        print(f"  [{o['organization']}] SKIPPED — curriculum '{o['curriculum_slug']}' already exists "
              f"(id={o['curriculum_id']}).")
        return
    print(f"  [{o['organization']}]  org_id={o['organization_id']}")
    print(f"     Curriculum  : {o['curriculum']} (slug={o['curriculum_slug']})")
    print(f"     Versions    : {list(o['versions'].keys())}")
    print(f"     Users       : {o['users']} (<role>@{o['email_domain']})")
    print(f"     Modules     : {o['modules']}   Projects: {o['projects']}   Assets: {o['assets']}")
    print(f"     Graph edges : {o['dependency_edges']}   2nd versions: {o['second_versions']}   Misaligned: {o['misaligned']}")
    print(f"     CCRs        : {o['change_requests']}   QA reviews: {o['qa_reviews']}   Approvals: {o['approvals']}")
    print(f"     Cohorts     : {o['cohorts']}   History events: {o['history_events']}")


async def _demo_enrich(session: AsyncSession) -> dict:
    """Demo-only: fork each org's primary curriculum to a v1.1.0 release.

    This is invoked from the seed CLI (``_main``) ONLY — never from ``seed()`` —
    so the test suite + golden fixtures (which seed via ``seed()``) are entirely
    unaffected. Its purpose is to give the live/hosted demo a *real version
    history*: a v1.1.0 that shares all of v1.0.0's unchanged content (structural
    sharing — the unchanged ``ContentVersion`` rows are referenced, not copied)
    while changing one asset and adding one, so the Changes (release-diff) view
    shows a non-trivial diff and the version model's payoff is visible.
    """
    from sqlalchemy import select

    from app.core.fork import Bump, ContentEdit, EdgeSpec, ForkChanges, NewAsset, fork
    from app.core.manifest import active_curriculum_version, version_members
    from app.models.curriculum import Curriculum
    from app.models.enums import AssetKind
    from app.models.org import Organization

    created = 0
    org_ids = [r[0] for r in (await session.execute(select(Organization.id))).all()]
    for org_id in org_ids:
        async with _bound_org(session, org_id):
            curricula = (await session.execute(select(Curriculum))).scalars().all()
            for cur in curricula:
                cv = await active_curriculum_version(session, cur.id)
                if cv is None:
                    continue
                members = await version_members(session, cv.id)
                if not members:
                    continue
                # Lineage prefix, e.g. "agentic-ai/v1" from "agentic-ai/v1/01/slides".
                prefix = "/".join(members[0].lineage_key.split("/")[:2])
                # Improve a lesson plan (content change) + add a capstone lab that
                # depends on it (a new asset + a new prerequisite edge).
                changed = next(
                    (m for m in members if "lesson_plan" in m.lineage_key), members[0]
                )
                new_key = f"{prefix}/capstone/integration_lab"
                changes = ForkChanges(
                    changed={
                        changed.lineage_key: ContentEdit(
                            content=(
                                "# Lesson Plan (v1.1.0)\n\n## Overview\n"
                                "Revised with clearer outcomes and an added hands-on "
                                "segment that leads into the new capstone lab.\n\n"
                                "## Outline\n- Motivation and context\n"
                                "- Core techniques (expanded)\n- Guided walkthrough\n"
                                "- Bridge to the capstone integration lab\n"
                            ),
                        )
                    },
                    added=[
                        NewAsset(
                            lineage_key=new_key,
                            kind=AssetKind.lab,
                            content=(
                                "# Capstone Integration Lab\n\n## Goal\n"
                                "Apply the full pipeline end-to-end on a realistic "
                                "scenario, integrating the techniques from this track.\n"
                            ),
                            section="Capstone: Integration",
                            week_index=99,
                            order=0,
                        )
                    ],
                    edges_added=[EdgeSpec(from_key=changed.lineage_key, to_key=new_key)],
                )
                await fork(session, cur.id, bump=Bump.minor, changes=changes)
                created += 1
            # Commit inside the org's tenant context (not after it) so the write
            # boundary always matches the org the rows were stamped with.
            await session.commit()
    return {"demo_versions_created": created}


async def _demo_review_ccr(session: AsyncSession) -> dict:
    """Demo-only: seed one mid-review change request per curriculum.

    CLI-only (``_main``). Creates an executable CCR (carrying a ``change_set``)
    that is already QA-passed and has ONE instructor approval — so the Review
    page is non-empty out of the box and a single architect/PM viewer can finish
    the gate (add the 2nd, distinct approval) and merge it live. Demonstrates the
    full PR loop without any setup.
    """
    from sqlalchemy import select

    from app.core.versioning.semver import BumpType
    from app.core.workflow.engine import record_approval, record_qa, submit_ccr
    from app.core.manifest import active_curriculum_version, version_members
    from app.models.curriculum import Curriculum
    from app.models.enums import AssetKind
    from app.models.org import Organization
    from app.models.user import User

    qa_pass = {
        "content_accuracy": 5, "alignment": 4, "prerequisites": 5,
        "consistency": 4, "instructor_support": 5, "student_experience": 5,
    }
    created = 0
    org_ids = [r[0] for r in (await session.execute(select(Organization.id))).all()]
    for org_id in org_ids:
        async with _bound_org(session, org_id):
            async def _user(role: str):
                return await session.scalar(select(User).where(User.role == role))

            # Author must be allowed to use instructor_override (instructor_lead
            # or architect — the router enforces this). Use instructor_lead so the
            # architect viewer stays a distinct, eligible 2nd approver. The seeded
            # approval comes from a plain `instructor` (an instructor-role approval
            # that isn't the author), satisfying the gate's instructor requirement.
            author = await _user("instructor_lead")
            instructor = await _user("instructor")
            qa = await _user("qa_lead")
            if not (author and instructor and qa):
                continue
            curricula = (await session.execute(select(Curriculum))).scalars().all()
            for cur in curricula:
                cv = await active_curriculum_version(session, cur.id)
                if cv is None:
                    continue
                members = await version_members(session, cv.id)
                if not members:
                    continue
                prefix = "/".join(members[0].lineage_key.split("/")[:2])
                anchor = next(
                    (m for m in members if "lesson_plan" in m.lineage_key), members[0]
                )
                new_key = f"{prefix}/proposed/evaluation_lab"
                change_set = {
                    "bump": "minor",
                    "changed": [],
                    "added": [{
                        "lineage_key": new_key, "kind": AssetKind.lab.value,
                        "content": "# Evaluation Lab (proposed)\n\nHands-on lab to "
                        "measure model quality with a rubric-scored harness.",
                        "metadata": None, "section": "Proposed: Evaluation",
                        "week_index": 98, "order": 0, "source_url": None,
                    }],
                    "removed": [],
                    "edges_added": [{
                        "from_key": anchor.lineage_key, "to_key": new_key,
                        "edge_type": "prerequisite", "validated_against_seq": None,
                    }],
                    "edges_removed": [],
                }
                ccr = await submit_ccr(
                    session, curriculum_id=cur.id, author_id=author.id,
                    title="Add a hands-on evaluation lab",
                    rationale="Reinforce the measurement/quality objective with an "
                    "applied, rubric-scored lab before the capstone.",
                    proposed_bump=BumpType.minor, affected_kinds={AssetKind.lab},
                    instructor_override=True,
                )
                ccr.change_set = change_set
                session.add(ccr)
                await session.flush()
                # QA pass + one instructor approval (the viewer adds the 2nd).
                await record_qa(
                    session, ccr=ccr, reviewer_id=qa.id,
                    dimension_scores=qa_pass, verdict="pass",
                )
                await record_approval(
                    session, ccr=ccr, approver_id=instructor.id,
                    role="instructor", decision="approve",
                )
                created += 1
            await session.commit()
    return {"demo_review_ccrs": created}


async def _demo_learner_enroll(session: AsyncSession) -> dict:
    """Demo-only: add a ``learner``-role user to Career Forge and enroll it in
    the released demo course, so the Course Player is demoable end-to-end.

    CLI-only (``_main``) — never called from ``seed()``, so the test suite and
    golden fixtures are unaffected (mirrors ``_demo_enrich`` / ``_demo_review_ccr``).
    Must run AFTER ``_demo_enrich``: enrollment pins the curriculum's
    ``active_content_version_id`` (the released ``CurriculumVersion`` the catalog
    serves), which ``fork()`` sets during ``_demo_enrich``. Idempotent: the user
    is get-or-created by email and the enrollment is guarded by
    (learner_id, curriculum_version_id).
    """
    from sqlalchemy import select

    from app.models.curriculum import Curriculum
    from app.models.learner import Enrollment
    from app.models.org import Organization
    from app.models.user import User

    org = await session.scalar(
        select(Organization).where(Organization.name == CAREER_FORGE_ORG_NAME)
    )
    if org is None:
        return {"learner_enrolled": False, "reason": "career-forge org missing"}

    async with _bound_org(session, org.id):
        # Get-or-create the learner user (email is globally unique).
        learner = await session.scalar(
            select(User).where(User.email == CAREER_FORGE_LEARNER["email"])
        )
        if learner is None:
            learner = User(
                email=CAREER_FORGE_LEARNER["email"],
                display_name=CAREER_FORGE_LEARNER["display_name"],
                role=CAREER_FORGE_LEARNER["role"],
                password_hash=hash_password(DEMO_PASSWORD),
                organization_id=org.id,
            )
            session.add(learner)
            await session.flush()

        # The released version the catalog serves = active_content_version_id.
        curriculum = await session.scalar(
            select(Curriculum).where(Curriculum.slug == CAREER_FORGE_CURRICULUM_SLUG)
        )
        version_id = (
            curriculum.active_content_version_id if curriculum is not None else None
        )
        if version_id is None:
            await session.commit()
            return {
                "learner_enrolled": False,
                "reason": "no released (active) content version",
                "learner_email": learner.email,
            }

        existing = await session.scalar(
            select(Enrollment).where(
                Enrollment.learner_id == learner.id,
                Enrollment.curriculum_version_id == version_id,
            )
        )
        if existing is None:
            session.add(
                Enrollment(learner_id=learner.id, curriculum_version_id=version_id)
            )
        await session.commit()

    return {
        "learner_enrolled": True,
        "learner_email": CAREER_FORGE_LEARNER["email"],
        "enrolled_version_id": str(version_id),
    }


async def _main() -> None:
    # No ambient tenant context: the seed sets context per org via use_org.
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        summary = await seed(session)
        # M3 cutover: immediately back-fill the immutable content model so the
        # seeded environment lands with a populated manifest — the ported read
        # paths (graph/dashboard/diff) then serve the new model, not the legacy
        # fallback. Idempotent + self-committing; runs per-org internally.
        backfill_counts = await backfill_content_model(session)
        # Demo-only enrichment: a real v1.1.0 release per curriculum (CLI-only;
        # never touches seed()/tests). Gives the hosted demo a version history.
        enrich_counts = await _demo_enrich(session)
        # Demo-only: a mid-review change request so Review tells its story
        # immediately (a single viewer can approve + merge it live).
        review_counts = await _demo_review_ccr(session)
        # Demo-only: a learner user enrolled in the released course so the
        # Course Player works end-to-end (must run AFTER _demo_enrich, which
        # sets active_content_version_id via fork()).
        learner_counts = await _demo_learner_enroll(session)
        enrich_counts = {**enrich_counts, **review_counts, **learner_counts}

    await engine.dispose()

    print("\n=== CurricMesh Multi-Org Seed Summary ===")
    for o in summary["orgs"]:
        _print_org(o)
    print(
        "\n  Immutable model back-filled: "
        + ", ".join(f"{k}={v}" for k, v in backfill_counts.items())
    )
    print(
        "  Demo enrichment: "
        + ", ".join(f"{k}={v}" for k, v in enrich_counts.items())
    )
    print(f"\n  Demo password : {summary['demo_password']}")
    print("  Demo logins   : <role>@careerforge.demo (Career Forge) | <role>@acme.demo (Acme Academy)")
    print("  Roles         : architect, program_manager, instructor_lead, instructor, qa_lead, devops")
    print("  Each login sees ONLY its own org's curriculum (tenant isolation).")
    print("=========================================\n")


if __name__ == "__main__":
    asyncio.run(_main())
