"""Version-to-version diff tests (core + endpoint).

Mirrors the seeded + back-filled org-pinned fixture (``tests/fork/conftest.py``).
Forks a version that adds one asset, adds one edge, and changes one asset's
content, then asserts the diff (core + HTTP) reports exactly that delta. Also
covers the default-to-parent base and the 404 paths.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.fork import (
    Bump,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    NewAsset,
    fork,
)
from app.core.manifest import active_curriculum_version
from app.core.version_diff import version_diff
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.routers.version_diff import get_version_diff
from tests.fork._helpers import members_by_key
from tests.fork.conftest import org_for_slug, org_session, seeded_engine  # noqa: F401

SLUG = "agentic-ai"


async def _curriculum(session) -> Curriculum:
    return await session.scalar(select(Curriculum).where(Curriculum.slug == SLUG))


async def _active(session, curriculum_id):
    av = await active_curriculum_version(session, curriculum_id)
    assert av is not None
    return av


async def _fork_with_delta(s, cur):
    """Fork active -> new: +1 asset, +1 edge (new asset depends on an existing
    member), and change 1 existing asset's content. Returns (parent, new, keys)."""
    parent = await _active(s, cur.id)
    members = await members_by_key(s, parent.id)
    changed_key = list(members)[0]
    edge_to_key = list(members)[1]  # existing member the new asset will depend on

    added_key = "versiondiff-added"
    new_v = await fork(
        s,
        cur.id,
        Bump.minor,
        ForkChanges(
            changed={changed_key: ContentEdit(content="DIFF: a new revision body")},
            added=[
                NewAsset(
                    lineage_key=added_key,
                    kind=AssetKind.lab,
                    content="added body",
                    section="Week 1",
                    week_index=1,
                )
            ],
            edges_added=[EdgeSpec(from_key=added_key, to_key=edge_to_key)],
        ),
    )
    await s.commit()
    return parent, new_v, {
        "changed": changed_key,
        "added": added_key,
        "edge_to": edge_to_key,
    }


# ---------------------------------------------------------------------------
# 1. Core version_diff reports exactly the delta.
# ---------------------------------------------------------------------------


async def test_version_diff_core_reports_delta(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent, new_v, keys = await _fork_with_delta(s, cur)

        diff = await version_diff(s, parent.id, new_v.id)

        assert len(diff.assets_added) == 1
        assert diff.assets_added[0].lineage_key == keys["added"]

        assert len(diff.assets_changed) == 1
        ch = diff.assets_changed[0]
        assert ch.lineage_key == keys["changed"]
        # The change advanced the selected revision by one seq.
        assert ch.to_seq == ch.from_seq + 1
        assert ch.from_hash != ch.to_hash

        assert len(diff.assets_removed) == 0

        assert len(diff.edges_added) == 1
        assert len(diff.edges_removed) == 0


# ---------------------------------------------------------------------------
# 2. Endpoint: diff against parent by default; legacy ids + labels.
# ---------------------------------------------------------------------------


async def test_version_diff_endpoint_defaults_to_parent(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent, new_v, keys = await _fork_with_delta(s, cur)

        # No explicit base → defaults to head's parent (== parent here).
        out = await get_version_diff(
            cur.id, new_v.id, base=None, current={}, db=s
        )
        assert out.base_version_id == parent.id
        assert out.head_version_id == new_v.id
        assert len(out.assets_added) == 1
        assert len(out.assets_changed) == 1
        assert len(out.edges_added) == 1
        # Labels are present (friendly strings, not blank).
        assert out.assets_added[0].label
        assert out.edges_added[0].from_label and out.edges_added[0].to_label

        # Explicit base equal to parent yields the same delta.
        out_explicit = await get_version_diff(
            cur.id, new_v.id, base=parent.id, current={}, db=s
        )
        assert len(out_explicit.assets_added) == 1
        assert len(out_explicit.assets_changed) == 1
        assert len(out_explicit.edges_added) == 1


async def test_version_diff_root_version_is_empty(seeded_engine):
    """A version with no parent (and no explicit base) yields an empty diff."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        from app.models.content_model import CurriculumVersion

        cur = await _curriculum(s)
        # Walk the parent chain from the active version up to the root.
        node = await _active(s, cur.id)
        while node.parent_version_id is not None:
            node = await s.scalar(
                select(CurriculumVersion).where(
                    CurriculumVersion.id == node.parent_version_id
                )
            )
        assert node.parent_version_id is None

        out = await get_version_diff(cur.id, node.id, base=None, current={}, db=s)
        assert out.base_version_id is None
        assert out.assets_added == []
        assert out.assets_changed == []
        assert out.edges_added == []


# ---------------------------------------------------------------------------
# 3. 404 paths.
# ---------------------------------------------------------------------------


async def test_version_diff_unknown_curriculum_is_404(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        with pytest.raises(HTTPException) as exc:
            await get_version_diff(
                uuid.uuid4(), uuid.uuid4(), base=None, current={}, db=s
            )
        assert exc.value.status_code == 404


async def test_version_diff_unknown_head_is_404(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        with pytest.raises(HTTPException) as exc:
            await get_version_diff(
                cur.id, uuid.uuid4(), base=None, current={}, db=s
            )
        assert exc.value.status_code == 404


async def test_version_diff_unknown_base_is_404(seeded_engine):
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        active = await _active(s, cur.id)
        with pytest.raises(HTTPException) as exc:
            await get_version_diff(
                cur.id, active.id, base=uuid.uuid4(), current={}, db=s
            )
        assert exc.value.status_code == 404
