"""Asset friendly-name resolver — single backend source of truth for display labels.

Assets carry no human ``name`` (only ``kind`` + ``key`` + a ``module_id`` /
``project_id`` container reference).  The UI needs readable labels like
"Week 3: Recursion · Coding Lab" instead of raw ``asset.key`` paths or UUIDs.

This module provides:
  - ``KIND_LABELS``: the canonical kind → display-label map.  The frontend
    mirrors this for its forms; keep the two in sync.
  - ``asset_display_names``: a batched (no N+1) resolver that joins each Asset
    to its Module or Project container and produces ``"{container} · {kind}"``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import select

from app.models.structure import Asset, Module, Project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Canonical display labels per AssetKind value.  Values not present here fall
# back to ``kind.title()`` in the resolver.
KIND_LABELS: dict[str, str] = {
    "lab": "Coding Lab",
    "lesson_plan": "Lesson Plan",
    "learning_objectives": "Learning Objectives",
    "references": "References",
    "starter": "Starter Code",
    "project": "Project",
    "slides": "Slides",
    "assessment": "Assessment",
    "rubric": "Rubric",
    "spec": "Spec",
}


def _kind_label(kind: object) -> str:
    """Resolve a kind (AssetKind enum or raw string) to its display label."""
    # AssetKind is a str-enum, so ``str(kind)`` would yield "AssetKind.lab".
    # Use ``.value`` when present to get the bare "lab".
    key = getattr(kind, "value", kind)
    key = str(key)
    return KIND_LABELS.get(key, key.title())


async def asset_display_names(
    db: "AsyncSession",
    asset_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Resolve each asset id to a friendly ``"{container} · {kind}"`` label.

    Batched: at most three queries total (assets, modules, projects) regardless
    of how many asset ids are passed — no N+1.

    The container is the asset's Module (``f"Week {index}: {focus}"``) or
    Project (``project.title``).  If the asset has neither container (or the
    module has no ``focus``), the asset's ``key`` is used as the container so a
    label is always produced.

    Args:
        db:        Active AsyncSession.
        asset_ids: Iterable of asset UUIDs (duplicates and unknowns tolerated).

    Returns:
        Mapping ``asset_id -> display label`` for every *known* asset id.
        Unknown ids are simply omitted.
    """
    ids = list({aid for aid in asset_ids})
    if not ids:
        return {}

    asset_result = await db.execute(select(Asset).where(Asset.id.in_(ids)))
    assets = asset_result.scalars().all()
    if not assets:
        return {}

    module_ids = {a.module_id for a in assets if a.module_id is not None}
    project_ids = {a.project_id for a in assets if a.project_id is not None}

    modules: dict[uuid.UUID, Module] = {}
    if module_ids:
        mod_result = await db.execute(
            select(Module).where(Module.id.in_(module_ids))
        )
        modules = {m.id: m for m in mod_result.scalars().all()}

    projects: dict[uuid.UUID, Project] = {}
    if project_ids:
        proj_result = await db.execute(
            select(Project).where(Project.id.in_(project_ids))
        )
        projects = {p.id: p for p in proj_result.scalars().all()}

    names: dict[uuid.UUID, str] = {}
    for asset in assets:
        container: str | None = None
        module = modules.get(asset.module_id) if asset.module_id else None
        if module is not None and module.focus:
            container = f"Week {module.index}: {module.focus}"
        else:
            project = projects.get(asset.project_id) if asset.project_id else None
            if project is not None:
                container = project.title

        if not container:
            container = asset.key

        names[asset.id] = f"{container} · {_kind_label(asset.kind)}"

    return names
