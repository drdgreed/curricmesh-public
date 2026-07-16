"""Precise-staleness tests — fork provenance capture + ``manifest_alignment_detail``.

Mirrors the seeded + back-filled org-pinned fixture used by the fork/release
suites (``tests/fork/conftest.py``). The active version of each seeded curriculum
is ``1.0.0`` with all-null edge provenance, so the detail layer falls back to the
timestamp rule and MUST agree with ``manifest_alignment`` (consistency). A fork
that adds a validated edge AND advances the prerequisite's content then exercises
the precise revision-delta path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.fork import (
    Bump,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    NewAsset,
    fork,
)
from app.core.manifest import (
    active_curriculum_version,
    manifest_alignment,
    manifest_alignment_detail,
    version_edges,
)
from app.models.content_model import ContentVersion, VersionEdge
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.routers.alignment import get_curriculum_alignment
from tests.fork._helpers import members_by_key
from tests.fork.conftest import org_for_slug, org_session, seeded_engine  # noqa: F401

SLUG = "agentic-ai"


async def _curriculum(session) -> Curriculum:
    return await session.scalar(select(Curriculum).where(Curriculum.slug == SLUG))


async def _active(session, curriculum_id):
    av = await active_curriculum_version(session, curriculum_id)
    assert av is not None
    return av


async def _seq_of(session, content_version_id) -> int:
    return await session.scalar(
        select(ContentVersion.seq).where(ContentVersion.id == content_version_id)
    )


# ---------------------------------------------------------------------------
# 1. fork() auto-captures validated_against_seq on a freshly-added edge.
# ---------------------------------------------------------------------------


async def test_fork_auto_sets_validated_against_seq(seeded_engine):
    """An added edge with no explicit provenance defaults validated_against_seq to
    the prerequisite's currently-selected seq in the new version."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        members = await members_by_key(s, parent.id)

        # Pick two members with no edge between them today.
        from_key, to_key = list(members)[:2]
        prereq_lineage_id = members[from_key].asset_id

        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(edges_added=[EdgeSpec(from_key=from_key, to_key=to_key)]),
        )
        await s.commit()

        # The prerequisite's selected seq in the NEW version.
        new_members = await members_by_key(s, new_v.id)
        expected_seq = await _seq_of(
            s, new_members[from_key].content_version_id
        )

        # The new edge carries that seq as its captured provenance.
        edge = await s.scalar(
            select(VersionEdge).where(
                VersionEdge.curriculum_version_id == new_v.id,
                VersionEdge.from_asset_id == prereq_lineage_id,
                VersionEdge.to_asset_id == new_members[to_key].asset_id,
            )
        )
        assert edge is not None
        assert edge.validated_against_seq == expected_seq


async def test_fork_explicit_validated_against_seq_is_preserved(seeded_engine):
    """An explicit validated_against_seq on an added edge is NOT overwritten."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        members = await members_by_key(s, parent.id)
        from_key, to_key = list(members)[:2]

        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(
                edges_added=[
                    EdgeSpec(from_key=from_key, to_key=to_key, validated_against_seq=0)
                ]
            ),
        )
        await s.commit()

        new_members = await members_by_key(s, new_v.id)
        edge = await s.scalar(
            select(VersionEdge).where(
                VersionEdge.curriculum_version_id == new_v.id,
                VersionEdge.from_asset_id == new_members[from_key].asset_id,
                VersionEdge.to_asset_id == new_members[to_key].asset_id,
            )
        )
        assert edge.validated_against_seq == 0


# ---------------------------------------------------------------------------
# 2. manifest_alignment_detail consistency with manifest_alignment (back-fill).
# ---------------------------------------------------------------------------


async def test_detail_set_equals_alignment_on_backfill(seeded_engine):
    """On the all-null back-filled seed, the detail layer's dependent set equals
    manifest_alignment exactly, and every detail is timestamp-mode."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        active = await _active(s, cur.id)

        misaligned = await manifest_alignment(s, active.id)
        details = await manifest_alignment_detail(s, active.id)

        # Every back-filled edge is null → every detail is a timestamp fallback.
        assert all(d.mode == "timestamp" for d in details)
        assert all(d.revision_delta is None for d in details)

        # The emitted dependent set is EXACTLY the misaligned set.
        assert {d.dependent_asset_id for d in details} == misaligned


# ---------------------------------------------------------------------------
# 3. Revision-delta staleness: advanced prerequisite past a validated edge.
# ---------------------------------------------------------------------------


async def test_revision_delta_reported_when_prereq_advances(seeded_engine):
    """Fork a version that adds a validated edge AND bumps the prerequisite's
    content; the detail reports revision mode with the correct positive delta. A
    freshly-validated (not-advanced) edge is NOT stale."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        members = await members_by_key(s, parent.id)
        from_key, to_key = list(members)[:2]

        # Fork #1: add an edge (auto-captures validated_against_seq = current seq).
        v1 = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(edges_added=[EdgeSpec(from_key=from_key, to_key=to_key)]),
        )
        await s.commit()

        v1_members = await members_by_key(s, v1.id)
        validated_seq = await _seq_of(s, v1_members[from_key].content_version_id)

        # Freshly-validated, not-advanced → NOT stale on this revision edge.
        details_v1 = await manifest_alignment_detail(s, v1.id)
        prereq_id = v1_members[from_key].asset_id
        dep_id = v1_members[to_key].asset_id
        rev_for_edge = [
            d
            for d in details_v1
            if d.prerequisite_asset_id == prereq_id
            and d.dependent_asset_id == dep_id
            and d.mode == "revision"
        ]
        assert rev_for_edge == [], "a just-validated edge must not be stale"

        # Fork #2: advance the PREREQUISITE's content by two revisions (the edge,
        # carried forward, keeps validated_against_seq from v1).
        v2 = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(changed={from_key: ContentEdit(content="prereq rev +1")}),
        )
        await s.commit()
        v3 = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(changed={from_key: ContentEdit(content="prereq rev +2")}),
        )
        await s.commit()

        v3_members = await members_by_key(s, v3.id)
        current_seq = await _seq_of(s, v3_members[from_key].content_version_id)
        assert current_seq > validated_seq

        details_v3 = await manifest_alignment_detail(s, v3.id)
        rev_details = [
            d
            for d in details_v3
            if d.prerequisite_asset_id == v3_members[from_key].asset_id
            and d.dependent_asset_id == v3_members[to_key].asset_id
            and d.mode == "revision"
        ]
        assert len(rev_details) == 1
        assert rev_details[0].revision_delta == current_seq - validated_seq
        assert rev_details[0].revision_delta > 0


# ---------------------------------------------------------------------------
# 4. Alignment endpoint — legacy ids + labels; 404 on unknown curriculum.
# ---------------------------------------------------------------------------


async def test_alignment_endpoint_returns_legacy_ids_and_labels(seeded_engine):
    """The endpoint returns items with legacy Asset ids + friendly labels."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        out = await get_curriculum_alignment(cur.id, current={}, db=s)

        # The seeded curriculum has stale (timestamp-mode) dependencies.
        assert len(out.items) > 0
        for item in out.items:
            assert isinstance(item.dependent_id, uuid.UUID)
            assert isinstance(item.prerequisite_id, uuid.UUID)
            assert item.dependent_label  # non-empty friendly label
            assert item.prerequisite_label
            assert item.mode in ("revision", "timestamp")
            if item.mode == "timestamp":
                assert item.revision_delta is None


async def test_alignment_endpoint_unknown_curriculum_is_404(seeded_engine):
    from fastapi import HTTPException

    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        with pytest.raises(HTTPException) as exc:
            await get_curriculum_alignment(uuid.uuid4(), current={}, db=s)
        assert exc.value.status_code == 404
