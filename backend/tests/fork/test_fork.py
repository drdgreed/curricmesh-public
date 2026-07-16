"""``fork()`` behavioral tests (spec §7, cases 1–10).

Each test seeds + back-fills its own engine (``seeded_engine`` fixture) and runs
inside the ``agentic-ai`` org's tenant context. The active version of every
seeded curriculum is ``1.0.0`` (status ``active``) with a parent ``0.9.0``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.content_hash import content_hash
from app.core.fork import (
    Bump,
    ConcurrentForkError,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    ForkValidationError,
    NewAsset,
    fork,
)
from app.core.manifest import active_curriculum_version, version_edges, version_members
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    ImmutableContentVersionError,
    LineageAsset,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from tests.fork._helpers import (
    content_of,
    counts,
    edge_key_set,
    member_content_rows,
    members_by_key,
)
from tests.fork.conftest import org_for_slug, org_session

SLUG = "agentic-ai"


async def _active(session, curriculum_id) -> CurriculumVersion:
    av = await active_curriculum_version(session, curriculum_id)
    assert av is not None
    return av


async def _curriculum(session) -> Curriculum:
    return await session.scalar(select(Curriculum).where(Curriculum.slug == SLUG))


# ---------------------------------------------------------------------------
# 1. Immutability of the parent version.
# ---------------------------------------------------------------------------


async def test_parent_version_is_immutable_after_fork(seeded_engine):
    """After a fork the parent's members/edges/content are byte-identical, and a
    ContentVersion still cannot be UPDATEd (guard raises)."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)

        # Snapshot the parent before the fork (members, edges, content bodies).
        before_members = await members_by_key(s, parent.id)
        before_edges = await edge_key_set(s, parent.id)
        before_content = {
            m.lineage_key: (await content_of(s, m.content_version_id)).content
            for m in before_members.values()
        }

        target_key = next(iter(before_members))
        await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(
                changed={target_key: ContentEdit(content="MUTATED BODY", metadata={"r": 1})}
            ),
        )
        await s.commit()

        # Parent unchanged.
        after_members = await members_by_key(s, parent.id)
        after_edges = await edge_key_set(s, parent.id)
        assert set(after_members) == set(before_members)
        assert after_edges == before_edges
        for key, m in after_members.items():
            # Same selected content row, same body — the parent never repointed.
            assert m.content_version_id == before_members[key].content_version_id
            assert (await content_of(s, m.content_version_id)).content == before_content[key]

        # The guard still bites: UPDATEing any ContentVersion raises.
        any_cv = await s.scalar(select(ContentVersion).limit(1))
        any_cv.content = "illegal edit"
        with pytest.raises(ImmutableContentVersionError):
            await s.flush()
        s.expunge(any_cv)  # discard the doomed pending state


# ---------------------------------------------------------------------------
# 2. Structural sharing — exactly one new ContentVersion for a 1-asset change.
# ---------------------------------------------------------------------------


async def test_structural_sharing_one_change_one_new_content(seeded_engine):
    """Changing exactly one in-version asset creates exactly ONE new
    ContentVersion (global delta == 1) and N member pointer rows; the other N-1
    contents are referenced, not duplicated."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        parent_members = await members_by_key(s, parent.id)
        n = len(parent_members)

        before = await counts(s)
        target_key = next(iter(parent_members))
        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(changed={target_key: ContentEdit(content="ONE NEW BODY")}),
        )
        await s.commit()
        after = await counts(s)

        # Exactly one new ContentVersion overall.
        assert after["content_versions"] - before["content_versions"] == 1
        # One new CurriculumVersion, N new members.
        assert after["curriculum_versions"] - before["curriculum_versions"] == 1
        assert after["version_members"] - before["version_members"] == n

        new_members = await members_by_key(s, new_v.id)
        assert len(new_members) == n
        # The changed member repointed; every other member SHARES the parent row.
        for key, m in new_members.items():
            if key == target_key:
                assert m.content_version_id != parent_members[key].content_version_id
            else:
                assert m.content_version_id == parent_members[key].content_version_id


# ---------------------------------------------------------------------------
# 3. Content-hash integrity (fsck) + dedup.
# ---------------------------------------------------------------------------


async def test_content_hash_integrity_and_dedup(seeded_engine):
    """Every new member's content_hash recomputes correctly (fsck), and re-using
    identical content for a change reuses the row (count delta 0)."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)

        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(
                added=[
                    NewAsset(
                        lineage_key="fsck-added",
                        kind=AssetKind.lab,
                        content="added body",
                        metadata={"k": "v"},
                        section="Week 1",
                        week_index=1,
                    )
                ]
            ),
        )
        await s.commit()

        # fsck: every member's stored hash == recompute from content/metadata/kind.
        for member, content in await member_content_rows(s, new_v.id):
            lineage = await s.scalar(
                select(LineageAsset).where(LineageAsset.id == member.asset_id)
            )
            expected = content_hash(lineage.kind.value, content.content, content.metadata_)
            assert content.content_hash == expected, f"fsck mismatch on {lineage.lineage_key}"

        # Dedup: a change whose new content equals the asset's existing content
        # reuses the existing ContentVersion (no new row).
        active = await _active(s, cur.id)  # the version we just forked into
        members = await members_by_key(s, active.id)
        a_key = next(iter(members))
        existing = await content_of(s, members[a_key].content_version_id)

        before = await counts(s)
        await fork(
            s,
            cur.id,
            Bump.patch,
            ForkChanges(
                changed={a_key: ContentEdit(content=existing.content, metadata=existing.metadata_)}
            ),
        )
        await s.commit()
        after = await counts(s)
        assert after["content_versions"] == before["content_versions"], (
            "identical content should reuse the existing ContentVersion (dedup)"
        )


# ---------------------------------------------------------------------------
# 4. Completeness — members/edges = parent − removed + added (changed repointed).
# ---------------------------------------------------------------------------


async def test_completeness_members_and_edges(seeded_engine):
    """new members == parent − removed + added (changed repointed); new edges ==
    parent − removed + added (with removed-asset edges dropped)."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        parent_members = await members_by_key(s, parent.id)
        parent_edges = await version_edges(s, parent.id)

        keys = list(parent_members)
        changed_key = keys[0]
        # Pick a removable asset that is NOT an endpoint of changed_key's edges,
        # so the expected edge math is simple: choose one with no edges at all.
        edge_endpoint_ids = {e.from_asset_id for e in parent_edges} | {
            e.to_asset_id for e in parent_edges
        }
        removable = next(
            (k for k, m in parent_members.items()
             if m.asset_id not in edge_endpoint_ids and k != changed_key),
            None,
        )
        assert removable is not None, "expected at least one edge-free removable asset"

        added_key = "completeness-added"
        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(
                changed={changed_key: ContentEdit(content="changed!")},
                added=[
                    NewAsset(
                        lineage_key=added_key,
                        kind=AssetKind.spec,
                        content="spec body",
                        section="Week 2",
                        week_index=2,
                    )
                ],
                removed={removable},
            ),
        )
        await s.commit()

        new_members = await members_by_key(s, new_v.id)
        expected_keys = (set(parent_members) - {removable}) | {added_key}
        assert set(new_members) == expected_keys
        # Changed asset repointed; untouched assets shared.
        assert new_members[changed_key].content_version_id != parent_members[changed_key].content_version_id
        for k in set(parent_members) - {removable, changed_key}:
            assert new_members[k].content_version_id == parent_members[k].content_version_id

        # Edges: the removed (edge-free) asset contributed no edges, so the new
        # edge set, mapped to lineage keys, equals the parent's edge set.
        def key_edges(members, edges):
            id_to_key = {m.asset_id: k for k, m in members.items()}
            return {
                (id_to_key.get(e.from_asset_id), id_to_key.get(e.to_asset_id), e.edge_type)
                for e in edges
            }

        new_edges = await version_edges(s, new_v.id)
        assert key_edges(new_members, new_edges) == key_edges(parent_members, parent_edges)


# ---------------------------------------------------------------------------
# 5. Acyclicity rejection — a cycle in edges_added raises + rolls back.
# ---------------------------------------------------------------------------


async def test_cycle_rejected_and_rolled_back(seeded_engine):
    """A fork whose edges introduce a cycle raises ForkValidationError and persists
    nothing (no new version/members/edges; active pointer unchanged)."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        members = await members_by_key(s, parent.id)
        a, b = list(members)[:2]

        before = await counts(s)
        before_ptr = (await _curriculum(s)).active_content_version_id

        with pytest.raises(ForkValidationError):
            await fork(
                s,
                cur.id,
                Bump.patch,
                ForkChanges(edges_added=[EdgeSpec(a, b), EdgeSpec(b, a)]),
            )
        # The SAVEPOINT rolled back; commit the (empty) outer tx to be sure.
        await s.commit()

        after = await counts(s)
        assert after == before, "a rejected fork must persist nothing"
        assert (await _curriculum(s)).active_content_version_id == before_ptr


# ---------------------------------------------------------------------------
# 6. Referential validity — dangling edge endpoint raises + rolls back.
# ---------------------------------------------------------------------------


async def test_referential_validity_dangling_edge(seeded_engine):
    """An edge whose endpoint isn't a member of the new version raises + rolls
    back (here: an edge into a just-removed asset)."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        members = await members_by_key(s, parent.id)
        a, b = list(members)[:2]

        before = await counts(s)
        # Remove `b`, then add an edge a -> b. `b` is no longer a member → invalid.
        with pytest.raises(ForkValidationError):
            await fork(
                s,
                cur.id,
                Bump.patch,
                ForkChanges(removed={b}, edges_added=[EdgeSpec(a, b)]),
            )
        await s.commit()
        assert await counts(s) == before


# ---------------------------------------------------------------------------
# 7. Concurrency CAS — a stale expected-active pointer raises, no partial write.
# ---------------------------------------------------------------------------


async def test_concurrent_fork_stale_expectation(seeded_engine):
    """Forking with a stale ``expected_active_id`` raises ConcurrentForkError and
    persists nothing."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        before = await counts(s)

        with pytest.raises(ConcurrentForkError):
            await fork(
                s,
                cur.id,
                Bump.minor,
                ForkChanges(),
                expected_active_id=uuid.uuid4(),  # never the real active id
            )
        await s.commit()
        assert await counts(s) == before


async def test_concurrent_fork_after_pointer_moved(seeded_engine):
    """Once a first fork moves the active pointer, a second fork that still expects
    the original active version is rejected by the live-pointer CAS."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        original_active = await _active(s, cur.id)

        # First fork succeeds and moves the pointer.
        await fork(s, cur.id, Bump.minor, ForkChanges())
        await s.commit()

        # Second fork staking the now-stale original active id must be rejected.
        before = await counts(s)
        with pytest.raises(ConcurrentForkError):
            await fork(
                s,
                cur.id,
                Bump.minor,
                ForkChanges(),
                expected_active_id=original_active.id,
            )
        await s.commit()
        assert await counts(s) == before


# ---------------------------------------------------------------------------
# 8. Tenant isolation — a fork in org A is invisible in org B.
# ---------------------------------------------------------------------------


async def test_tenant_isolation(seeded_engine):
    """A fork performed in org A creates nothing visible in org B; B's counts are
    unchanged."""
    a_oid = await org_for_slug(seeded_engine, "agentic-ai")
    b_oid = await org_for_slug(seeded_engine, "cloud-data-eng")
    assert a_oid != b_oid

    async with org_session(seeded_engine, b_oid) as sb:
        before_b = await counts(sb)

    async with org_session(seeded_engine, a_oid) as sa:
        cur = await sa.scalar(select(Curriculum).where(Curriculum.slug == "agentic-ai"))
        await fork(
            sa,
            cur.id,
            Bump.minor,
            ForkChanges(
                added=[
                    NewAsset(
                        lineage_key="org-a-only",
                        kind=AssetKind.lab,
                        content="x",
                        section="Week 1",
                        week_index=1,
                    )
                ]
            ),
        )
        await sa.commit()

    async with org_session(seeded_engine, b_oid) as sb:
        assert await counts(sb) == before_b, "org A's fork leaked into org B"
        # The added lineage key must not be visible under org B's filter.
        from app.models.content_model import LineageAsset

        leaked = await sb.scalar(
            select(LineageAsset).where(LineageAsset.lineage_key == "org-a-only")
        )
        assert leaked is None


# ---------------------------------------------------------------------------
# 9. End-to-end visibility — graph endpoint reflects the fork.
# ---------------------------------------------------------------------------


async def test_end_to_end_graph_reflects_fork(seeded_engine):
    """After fork+activate, ``active_curriculum_version`` resolves to the new
    version, the graph endpoint shows an added asset as a node, and a changed
    asset's selected content differs from the parent's."""
    from app.routers.graph import get_curriculum_graph

    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        parent = await _active(s, cur.id)
        parent_members = await members_by_key(s, parent.id)
        changed_key = next(iter(parent_members))
        parent_changed_content = await content_of(
            s, parent_members[changed_key].content_version_id
        )

        new_v = await fork(
            s,
            cur.id,
            Bump.minor,
            ForkChanges(
                changed={changed_key: ContentEdit(content="DIFFERENT CONTENT NOW")},
                added=[
                    NewAsset(
                        lineage_key="graph-added-node",
                        kind=AssetKind.lab,
                        content="new node body",
                        section="Week 1",
                        week_index=1,
                    )
                ],
            ),
        )
        await s.commit()

        # active now resolves to the new version (pointer path).
        assert (await _active(s, cur.id)).id == new_v.id

        # The added asset is a node in the live graph. Its node id is the
        # LineageAsset id (no legacy Asset backs it), so look it up by that id.
        new_members = await members_by_key(s, new_v.id)
        added_lineage_id = new_members["graph-added-node"].asset_id
        graph = await get_curriculum_graph(cur.id, current={}, db=s)
        node_ids = {n.id for n in graph.nodes}
        assert added_lineage_id in node_ids, "added asset missing from graph nodes"

        # The changed asset's selected content differs from the parent's.
        changed_now = await content_of(s, new_members[changed_key].content_version_id)
        assert changed_now.content != parent_changed_content.content
        assert changed_now.content == "DIFFERENT CONTENT NOW"


# ---------------------------------------------------------------------------
# 10. Failure-path rollback — a mid-fork validation failure leaves the DB intact.
# ---------------------------------------------------------------------------


async def test_failure_path_is_transactional(seeded_engine):
    """A validation failure injected mid-fork (an added asset wired into a cycle
    AND new content written first) leaves the DB byte-for-byte unchanged."""
    oid = await org_for_slug(seeded_engine, SLUG)
    async with org_session(seeded_engine, oid) as s:
        cur = await _curriculum(s)
        before = await counts(s)
        before_ptr = (await _curriculum(s)).active_content_version_id

        # This fork writes a new lineage + content + members BEFORE validation runs,
        # then fails acyclicity — proving the whole unit rolls back (not just the
        # last step). The added node forms a 2-cycle with an existing asset.
        active = await _active(s, cur.id)
        existing_key = next(iter(await members_by_key(s, active.id)))

        with pytest.raises(ForkValidationError):
            await fork(
                s,
                cur.id,
                Bump.minor,
                ForkChanges(
                    changed={existing_key: ContentEdit(content="written-then-rolled-back")},
                    added=[
                        NewAsset(
                            lineage_key="rollback-added",
                            kind=AssetKind.lab,
                            content="rollback body",
                            section="Week 1",
                            week_index=1,
                        )
                    ],
                    edges_added=[
                        EdgeSpec("rollback-added", existing_key),
                        EdgeSpec(existing_key, "rollback-added"),
                    ],
                ),
            )
        await s.commit()

        assert await counts(s) == before, "failed fork must be fully transactional"
        assert (await _curriculum(s)).active_content_version_id == before_ptr
        # The half-written content must not survive.
        leaked = await s.scalar(
            select(ContentVersion).where(ContentVersion.content == "written-then-rolled-back")
        )
        assert leaked is None
