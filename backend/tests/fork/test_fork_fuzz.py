"""Property-based fuzzing for ``fork()`` (spec §7 case 11).

``hypothesis`` is not installed in this environment, so we drive a deterministic
``random.Random(SEED)`` loop of many iterations. Each iteration:

1. Builds a **random topology** directly in the immutable model: a fresh
   curriculum with ``n`` lineage assets, each with one initial ContentVersion and
   a member in a fresh active ``CurriculumVersion``, plus a random **acyclic** set
   of edges (acyclic by construction: only ``i -> j`` with ``i < j``).
2. Generates a **random change-set**: some assets changed (new content, maybe
   identical → dedup), some added, some removed, plus random edge add/remove
   (which may or may not introduce a cycle).
3. Forks and asserts the invariants hold — *whether the fork succeeds or is
   rejected*:

   * **Immutability** — the parent version's members/content are unchanged.
   * **Completeness** — on success, members == parent − removed + added.
   * **Sharing count** — on success, the new-ContentVersion delta equals the
     number of *non-deduped* changed + added contents (structural sharing).
   * **Acyclicity preserved-or-rejected** — if the resulting edge set has a cycle
     the fork is rejected (``ForkValidationError``); otherwise it succeeds and the
     persisted edge DAG is acyclic.
   * **Integrity (fsck)** — on success, every new member's stored ``content_hash``
     recomputes from its content.

Seeded for reproducibility (``SEED``); bump iterations via ``ITERATIONS``.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.core.fork import (
    Bump,
    ContentEdit,
    EdgeSpec,
    ForkChanges,
    ForkValidationError,
    NewAsset,
    fork,
)
from app.core.manifest import version_edges, version_members
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from tests.fork.conftest import org_for_slug, org_session

SEED = 1337
ITERATIONS = 60
_KINDS = list(AssetKind)


async def _count_content_versions(s: AsyncSession) -> int:
    return (await s.scalar(select(func.count()).select_from(ContentVersion))) or 0


async def _build_random_curriculum(
    s: AsyncSession, rng: random.Random, tag: str
) -> tuple[Curriculum, CurriculumVersion, list[str]]:
    """Create a fresh curriculum + active CurriculumVersion with random topology.

    Returns ``(curriculum, active_version, lineage_keys)``. Edges are acyclic by
    construction (only forward ``i -> j`` for ``i < j``).
    """
    n = rng.randint(2, 6)
    cur = Curriculum(name=f"fuzz-{tag}", slug=f"fuzz-{tag}")
    s.add(cur)
    await s.flush()

    version = CurriculumVersion(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.active,
        parent_version_id=None,
    )
    s.add(version)
    await s.flush()

    keys: list[str] = []
    lineage_ids: list[uuid.UUID] = []
    for i in range(n):
        key = f"{tag}-asset-{i}"
        keys.append(key)
        kind = rng.choice(_KINDS)
        lineage = LineageAsset(kind=kind, lineage_key=key, source_url=None)
        s.add(lineage)
        await s.flush()
        lineage_ids.append(lineage.id)
        body = f"body-{tag}-{i}-v0"
        cv = ContentVersion(
            asset_id=lineage.id,
            seq=1,
            content=body,
            metadata_={"i": i},
            content_hash=content_hash(kind.value, body, {"i": i}),
            created_by=None,
        )
        s.add(cv)
        await s.flush()
        s.add(
            VersionMember(
                curriculum_version_id=version.id,
                asset_id=lineage.id,
                asset_version_id=cv.id,
                section=f"Week {i}",
                week_index=i,
                order=i,
            )
        )

    # Random acyclic edges: only forward i -> j (i < j) so no cycle by construction.
    from app.models.content_model import VersionEdge

    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.3:
                s.add(
                    VersionEdge(
                        curriculum_version_id=version.id,
                        from_asset_id=lineage_ids[i],
                        to_asset_id=lineage_ids[j],
                        edge_type="prerequisite",
                        validated_against_seq=None,
                    )
                )
    await s.flush()

    # Activate via the new-model pointer (so fork resolves THIS as active).
    cur.active_content_version_id = version.id
    await s.flush()
    return cur, version, keys


def _has_cycle(nodes: set, edges: set) -> bool:
    """True if the directed graph (edges as (from, to)) has a cycle."""
    adj: dict = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    for start in list(color):
        if color[start] != WHITE:
            continue
        stack = [(start, iter(adj.get(start, ())))]
        color[start] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == GREY:
                    return True
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    stack.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
    return False


async def test_fork_fuzz_invariants(seeded_engine):
    """Many random (topology, change-set) pairs preserve the fork invariants."""
    rng = random.Random(SEED)
    oid = await org_for_slug(seeded_engine, "agentic-ai")

    successes = 0
    rejections = 0

    async with org_session(seeded_engine, oid) as s:
        for it in range(ITERATIONS):
            cur, parent, keys = await _build_random_curriculum(s, rng, f"i{it}")
            await s.flush()

            # Snapshot parent (members + their content bodies) for immutability.
            parent_members = {m.lineage_key: m for m in await version_members(s, parent.id)}
            parent_bodies = {
                k: (
                    await s.scalar(
                        select(ContentVersion).where(
                            ContentVersion.id == m.content_version_id
                        )
                    )
                ).content
                for k, m in parent_members.items()
            }
            parent_edges = {
                (e.from_asset_id, e.to_asset_id, e.edge_type)
                for e in await version_edges(s, parent.id)
            }

            # --- Random change-set ---
            n = len(keys)
            removable = set(rng.sample(keys, k=rng.randint(0, max(0, n - 2))))
            changeable = [k for k in keys if k not in removable]
            changed: dict[str, ContentEdit] = {}
            n_real_changes = 0  # non-dedup (new content) changes
            for k in changeable:
                if rng.random() < 0.4:
                    if rng.random() < 0.3:
                        # Dedup: reuse the asset's CURRENT content exactly (the same
                        # content_hash → fork must reuse the row, no new content).
                        cv = await s.scalar(
                            select(ContentVersion).where(
                                ContentVersion.id == parent_members[k].content_version_id
                            )
                        )
                        changed[k] = ContentEdit(content=cv.content, metadata=cv.metadata_)
                    else:
                        changed[k] = ContentEdit(content=f"changed-{k}-{rng.random()}")
                        n_real_changes += 1

            added_specs: list[NewAsset] = []
            n_added = rng.randint(0, 2)
            added_keys: list[str] = []
            for a in range(n_added):
                ak = f"i{it}-added-{a}"
                added_keys.append(ak)
                added_specs.append(
                    NewAsset(
                        lineage_key=ak,
                        kind=rng.choice(_KINDS),
                        content=f"added-body-{ak}",
                        metadata={"added": a},
                        section="Added",
                        week_index=99,
                        order=a,
                    )
                )

            # Surviving member keys (for edge endpoint choice).
            surviving = [k for k in keys if k not in removable] + added_keys

            # Random edges added between surviving members (may create a cycle).
            edges_added: list[EdgeSpec] = []
            if len(surviving) >= 2:
                for _ in range(rng.randint(0, 3)):
                    a, b = rng.sample(surviving, 2)
                    edges_added.append(EdgeSpec(a, b))

            # Random edge removals (by parent edge → lineage keys).
            id_to_key = {m.asset_id: k for k, m in parent_members.items()}
            edges_removed: list[EdgeSpec] = []
            for (fa, ta, et) in parent_edges:
                if fa in id_to_key and ta in id_to_key and rng.random() < 0.3:
                    edges_removed.append(EdgeSpec(id_to_key[fa], id_to_key[ta], et))

            changes = ForkChanges(
                changed=changed,
                added=added_specs,
                removed=removable,
                edges_added=edges_added,
                edges_removed=edges_removed,
            )

            # Predict the resulting edge set (lineage KEYS) to know if a cycle is
            # expected. Parent edges minus removed (and minus removed-asset edges)
            # plus added.
            removed_edge_keys = {(e.from_key, e.to_key, e.edge_type) for e in edges_removed}
            surviving_set = set(surviving)
            predicted_edges_keys = set()
            for (fa, ta, et) in parent_edges:
                fk, tk = id_to_key.get(fa), id_to_key.get(ta)
                if fk in surviving_set and tk in surviving_set and (fk, tk, et) not in removed_edge_keys:
                    predicted_edges_keys.add((fk, tk))
            for e in edges_added:
                if e.from_key in surviving_set and e.to_key in surviving_set:
                    predicted_edges_keys.add((e.from_key, e.to_key))
            expect_cycle = _has_cycle(surviving_set, predicted_edges_keys)

            cv_before = await _count_content_versions(s)

            try:
                new_v = await fork(s, cur.id, Bump.minor, changes)
            except ForkValidationError:
                rejections += 1
                # On rejection the fork's SAVEPOINT rolled back. Parent must be
                # untouched and no new content persisted.
                assert await _count_content_versions(s) == cv_before
                after_parent = {m.lineage_key: m for m in await version_members(s, parent.id)}
                assert set(after_parent) == set(parent_members)
                for k, m in after_parent.items():
                    assert m.content_version_id == parent_members[k].content_version_id
                # A rejection should (almost always) be because a cycle was
                # predicted. (Other rejections are possible only on malformed
                # change-sets, which this generator never produces.)
                assert expect_cycle, "unexpected rejection on an acyclic change-set"
                continue

            successes += 1
            assert not expect_cycle, "a cyclic change-set was NOT rejected"

            # Immutability of parent.
            after_parent = {m.lineage_key: m for m in await version_members(s, parent.id)}
            assert set(after_parent) == set(parent_members)
            for k, m in after_parent.items():
                assert m.content_version_id == parent_members[k].content_version_id
                body_now = (
                    await s.scalar(
                        select(ContentVersion).where(ContentVersion.id == m.content_version_id)
                    )
                ).content
                assert body_now == parent_bodies[k]

            # Completeness.
            new_members = {m.lineage_key: m for m in await version_members(s, new_v.id)}
            expected_keys = (set(keys) - removable) | set(added_keys)
            assert set(new_members) == expected_keys

            # Sharing count: new ContentVersions == real changes + added.
            cv_after = await _count_content_versions(s)
            assert cv_after - cv_before == n_real_changes + n_added, (
                f"iter {it}: expected {n_real_changes + n_added} new content rows, "
                f"got {cv_after - cv_before}"
            )

            # Integrity (fsck) on the new version.
            rows = (
                await s.execute(
                    select(VersionMember, ContentVersion)
                    .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
                    .where(VersionMember.curriculum_version_id == new_v.id)
                )
            ).all()
            for member, content in rows:
                lineage = await s.scalar(
                    select(LineageAsset).where(LineageAsset.id == member.asset_id)
                )
                assert content.content_hash == content_hash(
                    lineage.kind.value, content.content, content.metadata_
                )

            # Acyclicity of the persisted DAG.
            persisted = {
                (e.from_asset_id, e.to_asset_id) for e in await version_edges(s, new_v.id)
            }
            assert not _has_cycle(
                {m.asset_id for m in new_members.values()}, persisted
            )

            await s.commit()

    # Both branches must be exercised for the fuzz to be meaningful.
    assert successes > 0, "fuzz produced no successful forks"
    assert rejections > 0, "fuzz never exercised the cycle-rejection path"
