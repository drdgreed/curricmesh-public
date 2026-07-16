"""Enrichment service: turn a detected-gap CCR into a placed draft proposal.

Reconstructs a GapFinding from the CCR's ai_research impact, builds a compact
curriculum-structure projection, runs place_gap + draft_frame (governed AI),
validates the placement target against the real structure, and writes
ccr.impact["enrichment"]. Advisory only: never mutates curriculum or CCR status.
Flushes; does NOT commit (the router commits).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import GapEnricher
from app.ai.schemas import (
    CurriculumStructure,
    GapFinding,
    Placement,
    StructureAsset,
    StructureModule,
    StructureProject,
)
from app.models.curriculum import Curriculum
from app.models.structure import Asset, Module, Project
from app.models.version import Version
from app.models.workflow import ChangeRequest


async def _resolve_version(session: AsyncSession, curriculum: Curriculum) -> Version | None:
    """Mirror research.py:_resolve_version — the current/active version to analyze."""
    if curriculum.current_version_id is not None:
        result = await session.execute(
            select(Version).where(Version.id == curriculum.current_version_id)
        )
        version = result.scalar_one_or_none()
        if version is not None:
            return version

    # Fall back to the latest version by semver (matching research.py exactly).
    result = await session.execute(
        select(Version)
        .where(Version.curriculum_id == curriculum.id)
        .order_by(Version.major.desc(), Version.minor.desc(), Version.patch.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_structure(session: AsyncSession, version: Version) -> CurriculumStructure:
    """Project a curriculum version into a compact structure for the placement prompt.

    Loads Module/Project/Asset metadata only — no content bodies (gs:// refs are
    not fetched). Assets are linked by module_id so the placement prompt can show
    which assets belong to which module.
    """
    modules = (
        await session.execute(
            select(Module).where(Module.version_id == version.id).order_by(Module.index)
        )
    ).scalars().all()

    projects = (
        await session.execute(
            select(Project).where(Project.version_id == version.id).order_by(Project.index)
        )
    ).scalars().all()

    module_ids = {m.id: m.index for m in modules}

    if module_ids:
        assets = (
            await session.execute(
                select(Asset).where(Asset.module_id.in_(list(module_ids.keys())))
            )
        ).scalars().all()
    else:
        assets = []

    return CurriculumStructure(
        modules=[StructureModule(index=m.index, focus=m.focus) for m in modules],
        projects=[StructureProject(index=p.index, title=p.title) for p in projects],
        assets=[
            StructureAsset(
                key=a.key,
                kind=a.kind.value,
                module_index=module_ids.get(a.module_id),
            )
            for a in assets
        ],
    )


def _finding_from_ccr(ccr: ChangeRequest) -> GapFinding:
    """Reconstruct a GapFinding from a gap-CCR's ai_research impact blob."""
    research = (
        (ccr.impact or {}).get("ai_research", {})
        if isinstance(ccr.impact, dict)
        else {}
    )
    topic = research.get("topic") or ccr.title.removeprefix("[AI] ").strip()
    return GapFinding(
        topic=topic,
        coverage_status=research.get("coverage_status", "partial"),
        evidence=list(research.get("citations", [])),
        proposed_bump=ccr.proposed_bump or "minor",
        rationale=ccr.rationale or "",
    )


def _validate_placement(placement: Placement, structure: CurriculumStructure) -> None:
    """Reject an invented target_ref — only real module indices / asset keys are valid.

    Raises ValueError if modify_module/modify_asset points at a ref not in the
    provided structure. new_module/new_asset require target_ref=null and bypass
    this check (no real ref to validate against).
    """
    if placement.target_kind == "modify_module":
        valid = {str(m.index) for m in structure.modules}
        if placement.target_ref not in valid:
            raise ValueError(
                f"placement target_ref {placement.target_ref!r} is not a real module index "
                f"(valid: {sorted(valid)})"
            )
    elif placement.target_kind == "modify_asset":
        valid = {a.key for a in structure.assets}
        if placement.target_ref not in valid:
            raise ValueError(
                f"placement target_ref {placement.target_ref!r} is not a real asset key"
            )
    # new_module / new_asset: target_ref should be null; nothing to validate against structure.


async def enrich_ccr(
    session: AsyncSession,
    *,
    ccr_id: uuid.UUID,
    enricher: GapEnricher,
) -> ChangeRequest:
    """Attach a placement + draft frame to a gap CCR.

    Reconstructs the GapFinding from impact["ai_research"], builds a compact
    CurriculumStructure projection, calls place_gap → validates → draft_frame,
    then writes the results into impact["enrichment"] (preserving other impact
    keys). Flushes but does NOT commit — the caller owns the transaction.

    Raises:
        ValueError: if the CCR or its curriculum is not found, the curriculum
                    has no version, or the AI returns an invented target_ref.
    """
    ccr = await session.get(ChangeRequest, ccr_id)
    if ccr is None:
        raise ValueError(f"CCR {ccr_id} not found")

    curriculum = await session.get(Curriculum, ccr.curriculum_id)
    if curriculum is None:
        raise ValueError(f"Curriculum {ccr.curriculum_id} not found")

    version = await _resolve_version(session, curriculum)
    if version is None:
        raise ValueError("curriculum has no version to place against")

    structure = await _build_structure(session, version)
    finding = _finding_from_ccr(ccr)

    placement = await enricher.place_gap(finding, structure)
    _validate_placement(placement, structure)  # rejects invented refs before the draft call
    frame = await enricher.draft_frame(finding, placement)

    impact = dict(ccr.impact or {})
    impact["enrichment"] = {
        "placement": placement.model_dump(),
        "draft_frame": frame.model_dump(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    ccr.impact = impact
    session.add(ccr)
    await session.flush()
    return ccr
