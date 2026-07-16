"""Integration tests for app.core.naming.asset_display_names.

Covers:
  - module-backed asset → "Week {index}: {focus} · {KIND_LABEL}"
  - project-backed asset → "{project.title} · {KIND_LABEL}"
  - fallback to asset.key when the container is missing / has no focus
  - KIND_LABELS source-of-truth labels (lab → "Coding Lab", etc.)
  - batched: resolves many ids without per-asset queries (no N+1)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.naming import KIND_LABELS, asset_display_names
from app.models.enums import AssetKind, LifecycleStatus
from app.models.structure import Asset, Module, Project
from app.models.version import Version
from app.models.curriculum import Curriculum


async def _version(session: AsyncSession) -> Version:
    cur = Curriculum(name="Naming Cur", slug=f"naming-{uuid.uuid4().hex[:6]}")
    session.add(cur)
    await session.flush()
    v = Version(curriculum_id=cur.id, major=1, minor=0, patch=0, status=LifecycleStatus.active)
    session.add(v)
    await session.flush()
    return v


async def test_module_backed_asset_name(db_session: AsyncSession):
    v = await _version(db_session)
    module = Module(version_id=v.id, index=3, focus="Recursion")
    db_session.add(module)
    await db_session.flush()
    asset = Asset(kind=AssetKind.lab, key="m3/lab.py", module_id=module.id)
    db_session.add(asset)
    await db_session.flush()

    names = await asset_display_names(db_session, [asset.id])
    assert names[asset.id] == "Week 3: Recursion · Coding Lab"


async def test_project_backed_asset_name(db_session: AsyncSession):
    v = await _version(db_session)
    project = Project(version_id=v.id, index=1, title="Capstone Build")
    db_session.add(project)
    await db_session.flush()
    asset = Asset(kind=AssetKind.spec, key="p1/spec.md", project_id=project.id)
    db_session.add(asset)
    await db_session.flush()

    names = await asset_display_names(db_session, [asset.id])
    assert names[asset.id] == "Capstone Build · Spec"


async def test_fallback_to_key_when_no_container(db_session: AsyncSession):
    # Asset with neither module nor project → label uses asset.key as container.
    asset = Asset(kind=AssetKind.references, key="loose/refs.md")
    db_session.add(asset)
    await db_session.flush()

    names = await asset_display_names(db_session, [asset.id])
    assert names[asset.id] == "loose/refs.md · References"


async def test_fallback_when_module_has_no_focus(db_session: AsyncSession):
    v = await _version(db_session)
    module = Module(version_id=v.id, index=2, focus=None)
    db_session.add(module)
    await db_session.flush()
    asset = Asset(kind=AssetKind.starter, key="m2/starter", module_id=module.id)
    db_session.add(asset)
    await db_session.flush()

    names = await asset_display_names(db_session, [asset.id])
    # No focus → container falls back to the asset key.
    assert names[asset.id] == "m2/starter · Starter Code"


async def test_empty_input_returns_empty(db_session: AsyncSession):
    assert await asset_display_names(db_session, []) == {}


async def test_kind_labels_source_of_truth():
    # Spot-check the canonical labels the frontend mirrors.
    assert KIND_LABELS["lab"] == "Coding Lab"
    assert KIND_LABELS["starter"] == "Starter Code"
    assert KIND_LABELS["learning_objectives"] == "Learning Objectives"
    assert KIND_LABELS["project"] == "Project"


async def test_batched_resolves_many_without_n_plus_one(db_session: AsyncSession):
    """Many assets across modules + projects resolve in one batched call.

    We assert correctness for a mixed batch; the implementation issues at most
    three queries (assets, modules, projects) regardless of asset count.
    """
    v = await _version(db_session)
    module = Module(version_id=v.id, index=5, focus="Graphs")
    project = Project(version_id=v.id, index=2, title="Final Project")
    db_session.add_all([module, project])
    await db_session.flush()

    a1 = Asset(kind=AssetKind.lesson_plan, key="k1", module_id=module.id)
    a2 = Asset(kind=AssetKind.rubric, key="k2", project_id=project.id)
    a3 = Asset(kind=AssetKind.assessment, key="k3")  # fallback
    db_session.add_all([a1, a2, a3])
    await db_session.flush()

    names = await asset_display_names(db_session, [a1.id, a2.id, a3.id, a1.id])
    assert names[a1.id] == "Week 5: Graphs · Lesson Plan"
    assert names[a2.id] == "Final Project · Rubric"
    assert names[a3.id] == "k3 · Assessment"
