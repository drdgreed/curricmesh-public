"""
Seed: import the synthetic "Widgets 101" curriculum as a SECOND curriculum
in the Career Forge org, from the manifests under ``backend/seed/agentic_mastery/``.

Unlike ``bootcamp_curriculum.py`` (synthetic content), this loads the actual 18
lesson markdown files as asset bodies, the real prerequisite graph (parsed from
each lesson's ``## Prerequisites`` section, with a sequential "spine" for lessons
that don't name explicit prereqs), the 3 projects (incl. capstone), and per-lesson
quizzes.

It writes ONLY the legacy tables (Curriculum / Version / Module / Project / Asset /
AssetVersion / DependencyEdge); ``backfill_content_model`` then derives the immutable
content model (LineageAsset / ContentVersion / CurriculumVersion / VersionMember /
VersionEdge). So it MUST run before backfill in the same session — see
``scripts/reseed_prod.py``.

SYNTHETIC / DEMO DATA ONLY — both the curriculum structure and the lesson prose
under ``agentic_mastery/`` are fabricated placeholder content for this mirror.

Usage (standalone, against a DB that already has the bootcamp seed / Career Forge org):
    cd backend
    ./venv/bin/python -m seed.load_agentic_mastery
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from app.config import settings
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import LineageAsset
from app.models import (
    Asset,
    AssetKind,
    AssetVersion,
    Cohort,
    Curriculum,
    DependencyEdge,
    LifecycleStatus,
    Module,
    Organization,
    Project,
    User,
    Version,
)
from app.tenant import use_org

CONTENT_DIR = os.path.join(_here, "agentic_mastery")
SLUG = "widgets-101"
NAME = "Widgets 101"
ORG_NAME = "Career Forge"


def _load(name: str) -> dict:
    with open(os.path.join(CONTENT_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _read_lesson(filename: str) -> str:
    with open(os.path.join(CONTENT_DIR, "lessons", filename), encoding="utf-8") as fh:
        return fh.read()


def _flatten(curriculum: dict) -> list[tuple[int, dict, dict]]:
    """Ordered [(global_index 1..18, milestone, module)] across all milestones."""
    out: list[tuple[int, dict, dict]] = []
    idx = 0
    for ms in curriculum["milestones"]:
        for mod in ms.get("modules", []):
            idx += 1
            out.append((idx, ms, mod))
    return out


def _code(module: dict) -> str:
    """Module 'number' '1.2' -> lesson code 'M1.2' (how prereqs reference lessons)."""
    return "M" + str(module["number"])


def _parse_prereqs(md_text: str) -> list[str]:
    """Lesson codes named in the '## Prerequisites' section (e.g. ['M0.2','M1.1'])."""
    m = re.search(r"^## Prerequisites\s*(.*?)(?:^## |\Z)", md_text, re.S | re.M)
    if not m:
        return []
    return sorted(set(re.findall(r"M\d\.\d", m.group(1))))


async def seed_agentic_mastery(session: AsyncSession) -> dict:
    """Create the 'Widgets 101' curriculum in the Career Forge org. Idempotent."""
    curriculum_json = _load("curriculum.json")
    projects_json = _load("projects.json")
    quizzes = _load("quizzes.json")
    flat = _flatten(curriculum_json)
    code_to_idx = {_code(mod): idx for idx, _, mod in flat}

    org = await session.scalar(select(Organization).where(Organization.name == ORG_NAME))
    if org is None:
        return {"skipped": True, "reason": f"org {ORG_NAME!r} not found — run bootcamp seed first"}

    with use_org(org.id):
        existing = await session.scalar(select(Curriculum).where(Curriculum.slug == SLUG))
        if existing is not None:
            return {"skipped": True, "curriculum_id": str(existing.id), "slug": SLUG}

        # --- Curriculum + versions (0.9.0 archived, 1.0.0 active) ---
        curriculum = Curriculum(name=NAME, slug=SLUG, current_version_id=None)
        session.add(curriculum)
        await session.flush()

        v_arch = Version(
            curriculum_id=curriculum.id, major=0, minor=9, patch=0,
            status=LifecycleStatus.archived, notes="Pre-release pilot; superseded by v1.0.0.",
        )
        session.add(v_arch)
        await session.flush()

        v_act = Version(
            curriculum_id=curriculum.id, major=1, minor=0, patch=0,
            status=LifecycleStatus.active,
            notes="Initial launch of Widgets 101 (synthetic placeholder curriculum).",
        )
        session.add(v_act)
        await session.flush()
        curriculum.current_version_id = v_act.id
        await session.flush()

        # deterministic monotonic clock so later lessons read as "more recently
        # changed" (drives the staleness DAG, same as the bootcamp seed).
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = [0]

        def _ts() -> datetime:
            clock[0] += 1
            return base + timedelta(hours=clock[0])

        # --- Modules (18) under the active version ---
        module_rec: dict[int, Module] = {}
        for idx, _ms, mod in flat:
            m = Module(version_id=v_act.id, index=idx, focus=mod["title"])
            session.add(m)
            module_rec[idx] = m
        await session.flush()

        # --- Projects (3) under the active version ---
        proj_rec: dict[int, Project] = {}
        for i, p in enumerate(projects_json["projects"], start=1):
            pr = Project(version_id=v_act.id, index=i, title=p["title"])
            session.add(pr)
            proj_rec[i] = pr
        await session.flush()

        # --- Lesson assets (lesson_plan, real markdown) + per-lesson quiz assets ---
        lesson_asset: dict[int, Asset] = {}
        assess_asset: dict[int, Asset] = {}
        for idx, ms, mod in flat:
            body = _read_lesson(mod["file"])
            a = Asset(
                kind=AssetKind.lesson_plan,
                key=f"{SLUG}/v1/{idx:02d}/lesson_plan",
                module_id=module_rec[idx].id,
                project_id=None,
            )
            session.add(a)
            await session.flush()
            session.add(AssetVersion(
                asset_id=a.id, major=1, minor=0, patch=0, status=LifecycleStatus.active,
                body_ref=body,
                metadata_={
                    "week": idx, "kind": "lesson_plan", "label": mod["title"],
                    "slug": mod["slug"], "milestone": ms.get("id"),
                    "studyTime": mod.get("studyTime"), "tier": mod.get("tier"),
                    "learningOutcomes": mod.get("learningOutcomes"),
                },
                created_at=_ts(),
            ))
            lesson_asset[idx] = a

            quiz = quizzes.get(mod["slug"])
            if quiz:
                qa = Asset(
                    kind=AssetKind.assessment,
                    key=f"{SLUG}/v1/{idx:02d}/assessment",
                    module_id=module_rec[idx].id, project_id=None,
                )
                session.add(qa)
                await session.flush()
                session.add(AssetVersion(
                    asset_id=qa.id, major=1, minor=0, patch=0, status=LifecycleStatus.active,
                    body_ref=json.dumps(quiz, ensure_ascii=False),
                    metadata_={"week": idx, "kind": "assessment", "label": mod["title"], "slug": mod["slug"]},
                    created_at=_ts(),
                ))
                assess_asset[idx] = qa
        await session.flush()

        # --- Project assets (brief + rubric) ---
        proj_primary: dict[int, Asset] = {}
        for i, p in enumerate(projects_json["projects"], start=1):
            pa = Asset(
                kind=AssetKind.lesson_plan,
                key=f"{SLUG}/v1/projects/{i:02d}/lesson_plan",
                module_id=None, project_id=proj_rec[i].id,
            )
            session.add(pa)
            await session.flush()
            session.add(AssetVersion(
                asset_id=pa.id, major=1, minor=0, patch=0, status=LifecycleStatus.active,
                body_ref=f"# {p['title']}\n\n{p.get('tagline', '')}\n\n{p.get('brief', '')}".strip(),
                metadata_={"project": i, "kind": "lesson_plan", "label": p["title"], "isCapstone": p.get("isCapstone", False)},
                created_at=_ts(),
            ))
            proj_primary[i] = pa

            rubric = p.get("rubric") or []
            if rubric:
                ra = Asset(
                    kind=AssetKind.rubric,
                    key=f"{SLUG}/v1/projects/{i:02d}/rubric",
                    module_id=None, project_id=proj_rec[i].id,
                )
                session.add(ra)
                await session.flush()
                w = round(1.0 / len(rubric), 2)
                criteria = [{"name": (r.get("label") or r.get("name") or str(r)), "weight": w} for r in rubric]
                session.add(AssetVersion(
                    asset_id=ra.id, major=1, minor=0, patch=0, status=LifecycleStatus.active,
                    body_ref=json.dumps({"criteria": criteria}),
                    metadata_={"project": i, "kind": "rubric", "label": p["title"]},
                    created_at=_ts(),
                ))
        await session.flush()

        # --- Dependency edges ---
        seen: set[tuple] = set()
        edges: list[DependencyEdge] = []

        def add_edge(frm: Asset | None, to: Asset | None, etype: str) -> None:
            if frm is None or to is None:
                return
            key = (frm.id, to.id, etype)
            if key in seen:
                return
            seen.add(key)
            edges.append(DependencyEdge(from_asset_id=frm.id, to_asset_id=to.id, edge_type=etype))

        for idx, _ms, mod in flat:
            body = _read_lesson(mod["file"])
            # explicit prereqs, filtered to earlier→later (keeps it a DAG)
            prs = [p for p in _parse_prereqs(body) if code_to_idx.get(p, 999) < idx]
            if prs:
                for p in prs:
                    add_edge(lesson_asset[code_to_idx[p]], lesson_asset[idx], "prerequisite")
            elif idx > 1:
                add_edge(lesson_asset[idx - 1], lesson_asset[idx], "prerequisite")  # spine
            if idx in assess_asset:
                add_edge(lesson_asset[idx], assess_asset[idx], "supports")

        # last lesson of each milestone → that milestone's project
        ms_last: dict[str, int] = {}
        for idx, ms, _mod in flat:
            ms_last[ms.get("id")] = idx
        for i, p in enumerate(projects_json["projects"], start=1):
            mid = p.get("milestone")
            if mid in ms_last:
                add_edge(lesson_asset[ms_last[mid]], proj_primary[i], "prerequisite")

        for e in edges:
            session.add(e)
        await session.flush()

        # --- Cohort (reuse existing org instructors) ---
        users = (await session.scalars(select(User))).all()

        def uid(substr: str) -> str | None:
            u = next((u for u in users if substr in u.email), None)
            return str(u.id) if u else None

        instructors = [x for x in (uid("instructor_lead@"), uid("instructor@")) if x]
        today = date.today()
        session.add(Cohort(
            curriculum_id=curriculum.id, version_id=v_act.id,
            name="Widgets 101 — Cohort 2026-Q3",
            start_date=today - timedelta(weeks=2), end_date=today + timedelta(weeks=16),
            instructors=instructors,
        ))
        await session.flush()

    return {
        "skipped": False, "curriculum_id": str(curriculum.id), "slug": SLUG,
        "lessons": len(flat), "quizzes": len(assess_asset),
        "projects": len(projects_json["projects"]), "edges": len(edges),
    }


def build_lesson_source_url_map(curriculum_json: dict) -> dict[str, str]:
    """Return {lineage_key: filename} for every lesson-plan asset in the JSON.

    lineage_key = ``f"{SLUG}/v1/{idx:02d}/lesson_plan"`` — the same key used
    when creating lesson Assets above.  This mapping is the single source of
    truth for both the seed sweep and ``scripts/backfill_source_urls.py``.
    """
    mapping: dict[str, str] = {}
    idx = 0
    for ms in curriculum_json.get("milestones", []):
        for mod in ms.get("modules", []):
            idx += 1
            key = f"{SLUG}/v1/{idx:02d}/lesson_plan"
            mapping[key] = mod["file"]
    return mapping


async def backfill_lesson_source_urls(
    session: AsyncSession, curriculum_json: dict
) -> dict[str, int]:
    """Set ``LineageAsset.source_url`` for lesson assets that are NULL or stale.

    Idempotent: only updates rows where source_url differs from the seed value.
    Requires an org-scoped session (RLS-filtered) so reads/writes stay
    within the correct tenant context.

    Returns ``{"set": N, "skipped": M}``.
    """
    mapping = build_lesson_source_url_map(curriculum_json)
    set_count = 0
    skipped = 0
    for lineage_key, filename in mapping.items():
        asset = await session.scalar(
            select(LineageAsset).where(LineageAsset.lineage_key == lineage_key)
        )
        if asset is None:
            skipped += 1
            continue
        if asset.source_url == filename:
            skipped += 1
            continue
        asset.source_url = filename
        set_count += 1
    if set_count:
        await session.flush()
    return {"set": set_count, "skipped": skipped}


async def _main() -> None:
    """Standalone run: assumes the bootcamp seed (Career Forge org) already exists."""
    url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1) \
        if settings.DATABASE_URL.startswith("postgresql://") else settings.DATABASE_URL
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        result = await seed_agentic_mastery(session)
        print("seed_agentic_mastery:", result)
        if not result.get("skipped"):
            counts = await backfill_content_model(session)
            print("backfill:", counts)
            curriculum_json = _load("curriculum.json")
            url_counts = await backfill_lesson_source_urls(session, curriculum_json)
            print("source_url backfill:", url_counts)
            await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
