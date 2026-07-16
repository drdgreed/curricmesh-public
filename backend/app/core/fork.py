"""The ``fork()`` primitive — derive a new immutable CurriculumVersion (M4).

``docs/specs/2026-06-06-immutable-version-model-design.md`` §6. A fork takes the
curriculum's currently-active manifest and produces a NEW
:class:`~app.models.content_model.CurriculumVersion` that:

* copies the parent's members (structural sharing — the unchanged ones point at
  the *same* immutable :class:`ContentVersion` rows, no duplication);
* for each CHANGED in-version asset, **appends** a new ``ContentVersion``
  (``seq = prev + 1``, recomputed ``content_hash``) and repoints the member —
  unless the new content hashes identical to an existing revision of that lineage,
  in which case that existing row is reused (dedup, §3);
* ADDS members (+ a new ``LineageAsset`` + its first ``ContentVersion``) for added
  assets, and DROPS members for removed assets;
* copies the parent's edges and applies the edge delta (``validated_against_seq``
  carried forward unchanged on copied edges);
* is **validated** before activation — the edge DAG is acyclic, every edge
  endpoint is a member, every member's content belongs to its asset, and every
  member has a placement (section/week_index);
* is **activated** via an optimistic compare-and-swap on
  ``Curriculum.active_content_version_id`` (stale expectation →
  :class:`ConcurrentForkError`), then frozen at ``status = active``.

Everything runs inside ONE nested transaction (a SAVEPOINT): any failure rolls
the whole fork back — no half-written version, no moved active pointer
(fail-closed). The ``ContentVersion`` immutability guard is never tripped because
fork only ever INSERTs content rows (never UPDATEs).

Tenant scoping is ambient: every new row is ``TenantScoped`` and write-stamps
``organization_id`` from ``current_org``, so a fork is invisible to other orgs —
the caller must already be inside the right tenant context (same contract as
every other write path).

Complexity is O(|changes| + |members| + |edges|): we copy the parent manifest
(unavoidable, it is a new immutable snapshot) and do constant work per change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


# ---------------------------------------------------------------------------
# Errors (module-local, mirroring DiffError / WorkflowError convention)
# ---------------------------------------------------------------------------


class ForkError(Exception):
    """Base class for every fork failure."""


class ForkValidationError(ForkError):
    """The proposed fork is invalid (cycle, dangling edge, bad placement, …).

    Raised *during* the fork's transaction, so it triggers a full rollback: no
    new version, members, or edges are persisted and the active pointer is
    untouched (fail-closed).
    """


class ConcurrentForkError(ForkError):
    """The active pointer moved out from under an optimistic fork (CAS failure).

    Raised when the curriculum's ``active_content_version_id`` no longer equals
    the value the fork started from, i.e. another fork won the race. The caller
    should re-read the active version and retry. Like ``ForkValidationError``,
    this aborts the whole fork — nothing is persisted.
    """


# ---------------------------------------------------------------------------
# Semver bump
# ---------------------------------------------------------------------------


class Bump(str, Enum):
    """How to advance the semver of the active version into the new version."""

    major = "major"
    minor = "minor"
    patch = "patch"


def _bumped_semver(
    major: int, minor: int, patch: int, bump: Bump
) -> tuple[int, int, int]:
    """Return the (major, minor, patch) after applying ``bump`` (standard rules)."""
    if bump is Bump.major:
        return (major + 1, 0, 0)
    if bump is Bump.minor:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


# ---------------------------------------------------------------------------
# Change-set value objects (the ergonomic ``changes`` input shape, §6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentEdit:
    """New content (+ metadata) for an existing in-version asset (a CHANGE).

    The asset is identified out-of-band by its lineage key (the dict key in
    :attr:`ForkChanges.changed`). Placement is inherited from the parent member
    unless overridden here.
    """

    content: str | None
    metadata: dict | None = None
    # Optional placement override (defaults: carry the parent member's placement).
    section: str | None = None
    week_index: int | None = None
    order: int | None = None


@dataclass(frozen=True)
class NewAsset:
    """A brand-new asset to ADD to the new version (new lineage + first content)."""

    lineage_key: str
    kind: AssetKind
    content: str | None
    metadata: dict | None = None
    section: str = ""
    week_index: int = 0
    order: int = 0
    source_url: str | None = None


@dataclass(frozen=True)
class EdgeSpec:
    """A prerequisite edge identified by the lineage keys of its endpoints.

    ``from_key`` is the prerequisite, ``to_key`` the dependent (``from -> to``
    means *to depends on from*) — the same direction the back-fill and graph use.
    """

    from_key: str
    to_key: str
    edge_type: str = "prerequisite"
    validated_against_seq: int | None = None


@dataclass
class ForkChanges:
    """The delta a fork applies to the active manifest.

    All keys are **lineage keys** (stable, version-independent). Empty fields mean
    "no change of that kind", so a no-op fork (just a semver bump that snapshots
    the parent) is ``ForkChanges()``.
    """

    changed: dict[str, ContentEdit] = field(default_factory=dict)
    added: list[NewAsset] = field(default_factory=list)
    removed: set[str] = field(default_factory=set)
    edges_added: list[EdgeSpec] = field(default_factory=list)
    edges_removed: list[EdgeSpec] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal working rows (lineage-key-addressable view of the new manifest)
# ---------------------------------------------------------------------------


@dataclass
class _MemberDraft:
    """A member of the new version, addressable by lineage key while building."""

    lineage_id: uuid.UUID
    lineage_key: str
    content_version_id: uuid.UUID
    section: str
    week_index: int
    order: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fork(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    bump: Bump,
    changes: ForkChanges | None = None,
    *,
    expected_active_id: uuid.UUID | None = None,
) -> CurriculumVersion:
    """Fork the curriculum's active version into a new, activated CurriculumVersion.

    Steps (all inside one SAVEPOINT — fail-closed):

    1. Resolve the active version (the CAS expectation) and its members/edges.
    2. Create the new draft ``CurriculumVersion`` (semver bumped, ``parent`` =
       active, ``status = draft``).
    3. Copy + mutate members (changed → new/dedup content, added → new lineage +
       first content, removed → dropped) and edges (copy + delta).
    4. Validate: acyclicity, referential validity, placement consistency.
    5. Activate: CAS ``Curriculum.active_content_version_id`` from the expected
       prior value, then freeze ``status = active``.

    ``expected_active_id`` is the optimistic-concurrency token: the version the
    caller *believes* is active. When supplied and it does not match the version
    actually resolved as active at fork time, the fork aborts with
    :class:`ConcurrentForkError` (another fork won the race). When omitted, the
    fork trusts the currently-resolved active version as its own expectation
    (still CAS-guarded against the live pointer at activation).

    Returns the new (active) ``CurriculumVersion``. Raises
    :class:`ForkValidationError` on an invalid change-set or
    :class:`ConcurrentForkError` if the active pointer moved — in both cases the
    SAVEPOINT is rolled back and nothing is persisted.
    """
    changes = changes or ForkChanges()

    # Run the entire fork inside a SAVEPOINT so any raise rolls back ONLY the
    # fork's writes, atomically, whether or not the caller wraps us in a bigger
    # transaction. On success the SAVEPOINT is released; the caller still owns
    # the outer commit.
    async with session.begin_nested():
        curriculum = await session.scalar(
            select(Curriculum).where(Curriculum.id == curriculum_id)
        )
        if curriculum is None:
            raise ForkValidationError(f"curriculum {curriculum_id} not found")

        active = await _resolve_active(session, curriculum)
        if active is None:
            raise ForkValidationError(
                f"curriculum {curriculum_id} has no active version to fork"
            )
        # Optimistic-concurrency guard: if the caller staked an expectation and it
        # disagrees with reality, bail before writing anything.
        if expected_active_id is not None and expected_active_id != active.id:
            raise ConcurrentForkError(
                f"stale active expectation for curriculum {curriculum_id} "
                f"(caller expected {expected_active_id}, resolved {active.id})"
            )
        resolved_active_id = active.id

        # --- 2. New draft CurriculumVersion (bumped from the active one). ---
        new_major, new_minor, new_patch = _bumped_semver(
            active.major, active.minor, active.patch, bump
        )
        if await _semver_exists(
            session, curriculum_id, new_major, new_minor, new_patch
        ):
            raise ForkValidationError(
                f"a version {new_major}.{new_minor}.{new_patch} already exists "
                f"for curriculum {curriculum_id}"
            )
        new_version = CurriculumVersion(
            curriculum_id=curriculum_id,
            major=new_major,
            minor=new_minor,
            patch=new_patch,
            status=LifecycleStatus.draft,
            parent_version_id=active.id,
        )
        session.add(new_version)
        await session.flush()

        # --- 3. Members + edges. ---
        member_drafts = await _build_members(
            session, parent_version_id=active.id, changes=changes
        )
        await _build_edges(
            session,
            new_version_id=new_version.id,
            parent_version_id=active.id,
            member_drafts=member_drafts,
            changes=changes,
        )

        # Persist the member drafts as real VersionMember rows.
        for d in member_drafts.values():
            session.add(
                VersionMember(
                    curriculum_version_id=new_version.id,
                    asset_id=d.lineage_id,
                    asset_version_id=d.content_version_id,
                    section=d.section,
                    week_index=d.week_index,
                    order=d.order,
                )
            )
        await session.flush()

        # --- 4. Validate the assembled new version (before activation). ---
        await _validate(session, new_version.id)

        # --- 5. Activate: optimistic CAS on the active pointer, then freeze. ---
        await _activate(session, curriculum_id, new_version, resolved_active_id)

    # SAVEPOINT released. Refresh so callers see the committed-in-savepoint state.
    await session.refresh(new_version)
    return new_version


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


async def _resolve_active(
    session: AsyncSession, curriculum: Curriculum
) -> CurriculumVersion | None:
    """The active version to fork from = the manifest resolver's active version.

    Imported lazily to avoid a module import cycle (manifest imports nothing from
    fork, but keeping the dependency one-directional and lazy is cheap insurance).
    """
    from app.core.manifest import active_curriculum_version

    return await active_curriculum_version(session, curriculum.id)


async def _semver_exists(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    major: int,
    minor: int,
    patch: int,
) -> bool:
    existing = await session.scalar(
        select(CurriculumVersion.id).where(
            CurriculumVersion.curriculum_id == curriculum_id,
            CurriculumVersion.major == major,
            CurriculumVersion.minor == minor,
            CurriculumVersion.patch == patch,
        )
    )
    return existing is not None


async def _build_members(
    session: AsyncSession,
    parent_version_id: uuid.UUID,
    changes: ForkChanges,
) -> dict[str, _MemberDraft]:
    """Produce the new version's members keyed by lineage key.

    Copy parent members (structural sharing), repoint changed ones at a new/deduped
    ContentVersion, drop removed ones, and append added ones (new lineage + first
    content). Returns ``{lineage_key: _MemberDraft}``.
    """
    # Parent members joined to their lineage (for the key) — the copy base.
    rows = (
        await session.execute(
            select(VersionMember, LineageAsset)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == parent_version_id)
        )
    ).all()

    drafts: dict[str, _MemberDraft] = {}
    lineage_by_key: dict[str, LineageAsset] = {}
    for member, lineage in rows:
        lineage_by_key[lineage.lineage_key] = lineage
        if lineage.lineage_key in changes.removed:
            continue  # DROP: not a member of the new version.
        drafts[lineage.lineage_key] = _MemberDraft(
            lineage_id=lineage.id,
            lineage_key=lineage.lineage_key,
            content_version_id=member.asset_version_id,
            section=member.section,
            week_index=member.week_index,
            order=member.order,
        )

    # Validate the change-set references real parent members (clear errors early).
    unknown_removed = changes.removed - set(lineage_by_key)
    if unknown_removed:
        raise ForkValidationError(
            f"removed assets not in the parent version: {sorted(unknown_removed)}"
        )
    unknown_changed = set(changes.changed) - set(lineage_by_key)
    if unknown_changed:
        raise ForkValidationError(
            f"changed assets not in the parent version: {sorted(unknown_changed)}"
        )
    overlap = set(changes.changed) & changes.removed
    if overlap:
        raise ForkValidationError(
            f"assets both changed and removed: {sorted(overlap)}"
        )

    # CHANGED: append (or dedup-reuse) a ContentVersion and repoint the member.
    for key, edit in changes.changed.items():
        lineage = lineage_by_key[key]
        new_cv = await _content_version_for_edit(session, lineage, edit)
        d = drafts[key]
        d.content_version_id = new_cv.id
        if edit.section is not None:
            d.section = edit.section
        if edit.week_index is not None:
            d.week_index = edit.week_index
        if edit.order is not None:
            d.order = edit.order

    # ADDED: new lineage + first ContentVersion + member.
    for spec in changes.added:
        if spec.lineage_key in drafts or spec.lineage_key in lineage_by_key:
            raise ForkValidationError(
                f"added asset {spec.lineage_key!r} already exists in the "
                "parent version"
            )
        lineage = LineageAsset(
            kind=spec.kind,
            lineage_key=spec.lineage_key,
            source_url=spec.source_url,
        )
        session.add(lineage)
        await session.flush()
        cv = await _append_content_version(
            session, lineage, spec.kind, spec.content, spec.metadata
        )
        drafts[spec.lineage_key] = _MemberDraft(
            lineage_id=lineage.id,
            lineage_key=spec.lineage_key,
            content_version_id=cv.id,
            section=spec.section,
            week_index=spec.week_index,
            order=spec.order,
        )

    return drafts


async def _content_version_for_edit(
    session: AsyncSession, lineage: LineageAsset, edit: ContentEdit
) -> ContentVersion:
    """The ContentVersion a CHANGE should point at (deduped, else appended)."""
    return await _append_content_version(
        session, lineage, lineage.kind, edit.content, edit.metadata
    )


async def _append_content_version(
    session: AsyncSession,
    lineage: LineageAsset,
    kind: AssetKind,
    content: str | None,
    metadata: dict | None,
) -> ContentVersion:
    """Append a new immutable ContentVersion for ``lineage`` — or reuse an
    identical existing one (dedup by ``content_hash``; §3 structural sharing).

    ``seq`` is the next free value for this lineage. Never UPDATEs an existing
    row (so the immutability guard is never tripped): a new revision is a new row.
    """
    ch = content_hash(kind.value, content, metadata)

    # Dedup: if this lineage already has a row with this exact content, reuse it.
    existing = await session.scalar(
        select(ContentVersion).where(
            ContentVersion.asset_id == lineage.id,
            ContentVersion.content_hash == ch,
        )
    )
    if existing is not None:
        return existing

    max_seq = await session.scalar(
        select(ContentVersion.seq)
        .where(ContentVersion.asset_id == lineage.id)
        .order_by(ContentVersion.seq.desc())
        .limit(1)
    )
    cv = ContentVersion(
        asset_id=lineage.id,
        seq=(max_seq or 0) + 1,
        content=content or "",
        metadata_=metadata,
        content_hash=ch,
        created_by=None,
    )
    session.add(cv)
    await session.flush()
    return cv


async def _build_edges(
    session: AsyncSession,
    new_version_id: uuid.UUID,
    parent_version_id: uuid.UUID,
    member_drafts: dict[str, _MemberDraft],
    changes: ForkChanges,
) -> None:
    """Copy the parent's edges (validated_against_seq carried forward) + delta.

    Edges are keyed on the *logical* lineage assets. A copied edge is kept only if
    both endpoints are still members of the new version (a removed asset's edges
    drop out — matching the in-version edge invariant). The delta adds/removes
    edges by lineage-key endpoints.
    """
    # lineage_id -> lineage_key, for the parent edges (resolve keys to apply delta).
    parent_member_rows = (
        await session.execute(
            select(VersionMember.asset_id, LineageAsset.lineage_key)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == parent_version_id)
        )
    ).all()
    key_by_lineage_id = {lid: key for lid, key in parent_member_rows}

    lineage_id_by_key = {key: d.lineage_id for key, d in member_drafts.items()}

    # Edge identity for the delta: (from_key, to_key, edge_type).
    def _spec_key(from_key: str, to_key: str, edge_type: str) -> tuple[str, str, str]:
        return (from_key, to_key, edge_type)

    removed_keys = {
        _spec_key(s.from_key, s.to_key, s.edge_type) for s in changes.edges_removed
    }

    # Copy parent edges that survive (both endpoints still members, not removed).
    kept: dict[tuple[str, str, str], VersionEdge] = {}
    parent_edges = (
        (
            await session.execute(
                select(VersionEdge).where(
                    VersionEdge.curriculum_version_id == parent_version_id
                )
            )
        )
        .scalars()
        .all()
    )
    for e in parent_edges:
        from_key = key_by_lineage_id.get(e.from_asset_id)
        to_key = key_by_lineage_id.get(e.to_asset_id)
        if from_key is None or to_key is None:
            continue  # endpoint not a parent member (shouldn't happen)
        if from_key not in member_drafts or to_key not in member_drafts:
            continue  # an endpoint was removed → edge drops out
        sk = _spec_key(from_key, to_key, e.edge_type)
        if sk in removed_keys:
            continue  # explicitly removed by the delta
        kept[sk] = VersionEdge(
            curriculum_version_id=new_version_id,
            from_asset_id=lineage_id_by_key[from_key],
            to_asset_id=lineage_id_by_key[to_key],
            edge_type=e.edge_type,
            validated_against_seq=e.validated_against_seq,  # carried forward
        )

    # Selected seq per lineage key in the NEW version (the member's
    # ContentVersion.seq). Used to auto-capture an added edge's provenance:
    # authoring an edge means "validated against the prerequisite as it is now".
    selected_seq_by_key = await _selected_seq_by_key(session, member_drafts)

    # Apply added edges (endpoints must be members of the new version).
    for s in changes.edges_added:
        if s.from_key not in member_drafts or s.to_key not in member_drafts:
            raise ForkValidationError(
                f"edge {s.from_key!r} -> {s.to_key!r} references an asset that is "
                "not a member of the new version"
            )
        # Auto-capture provenance: a freshly-authored edge with no explicit
        # ``validated_against_seq`` is validated against the prerequisite's
        # (``from_key``'s) currently-selected seq in this new version. If the
        # prerequisite has no resolvable selected seq (shouldn't happen — it is a
        # member), leave it null and fall back to the timestamp rule.
        validated_against_seq = s.validated_against_seq
        if validated_against_seq is None:
            validated_against_seq = selected_seq_by_key.get(s.from_key)
        sk = _spec_key(s.from_key, s.to_key, s.edge_type)
        kept[sk] = VersionEdge(
            curriculum_version_id=new_version_id,
            from_asset_id=lineage_id_by_key[s.from_key],
            to_asset_id=lineage_id_by_key[s.to_key],
            edge_type=s.edge_type,
            validated_against_seq=validated_against_seq,
        )

    for edge in kept.values():
        session.add(edge)
    await session.flush()


async def _selected_seq_by_key(
    session: AsyncSession, member_drafts: dict[str, _MemberDraft]
) -> dict[str, int]:
    """``{lineage_key: ContentVersion.seq}`` for each draft's selected content.

    Resolves the seq of the ContentVersion each member draft points at — the
    new version's "currently-selected revision" for that lineage asset. One
    batched query over the drafts' content-version ids.
    """
    cv_id_by_key = {key: d.content_version_id for key, d in member_drafts.items()}
    cv_ids = list(cv_id_by_key.values())
    if not cv_ids:
        return {}
    seq_by_cv_id = dict(
        (
            await session.execute(
                select(ContentVersion.id, ContentVersion.seq).where(
                    ContentVersion.id.in_(cv_ids)
                )
            )
        ).all()
    )
    return {
        key: seq_by_cv_id[cv_id]
        for key, cv_id in cv_id_by_key.items()
        if cv_id in seq_by_cv_id
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def _validate(session: AsyncSession, version_id: uuid.UUID) -> None:
    """Validate the assembled new version. Raises ``ForkValidationError``.

    Checks (read back from what we just persisted, so we validate the real rows):

    * **Referential validity** — every member's ``content_version`` belongs to the
      member's asset; every edge endpoint is a member of this version.
    * **Placement consistency** — every member has a non-empty ``section`` and a
      ``week_index`` (the model NOT NULLs them, but we assert the contract).
    * **Acyclicity** — the edge DAG (on logical assets) has no cycle.
    """
    member_rows = (
        await session.execute(
            select(VersionMember, ContentVersion)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .where(VersionMember.curriculum_version_id == version_id)
        )
    ).all()

    member_asset_ids: set[uuid.UUID] = set()
    for member, content in member_rows:
        member_asset_ids.add(member.asset_id)
        # Referential: the selected content must belong to the member's asset.
        if content.asset_id != member.asset_id:
            raise ForkValidationError(
                f"member {member.asset_id} points at content {content.id} that "
                f"belongs to a different asset ({content.asset_id})"
            )
        # Placement: section present + week_index present.
        if member.section is None or member.section == "":
            raise ForkValidationError(
                f"member {member.asset_id} has no section (placement incomplete)"
            )
        if member.week_index is None:
            raise ForkValidationError(
                f"member {member.asset_id} has no week_index (placement incomplete)"
            )

    edges = (
        (
            await session.execute(
                select(VersionEdge).where(
                    VersionEdge.curriculum_version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )

    # Referential: every edge endpoint must be a member of this version.
    adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
    for e in edges:
        if e.from_asset_id not in member_asset_ids:
            raise ForkValidationError(
                f"edge from-endpoint {e.from_asset_id} is not a member of the "
                "new version"
            )
        if e.to_asset_id not in member_asset_ids:
            raise ForkValidationError(
                f"edge to-endpoint {e.to_asset_id} is not a member of the new "
                "version"
            )
        adjacency.setdefault(e.from_asset_id, []).append(e.to_asset_id)

    _assert_acyclic(member_asset_ids, adjacency)


def _assert_acyclic(
    nodes: "Iterable[uuid.UUID]",
    adjacency: dict[uuid.UUID, list[uuid.UUID]],
) -> None:
    """Raise ``ForkValidationError`` if the directed graph has a cycle.

    Iterative DFS with a three-color marking (white/grey/black). A grey→grey edge
    is a back edge → cycle. Iterative (not recursive) so deep chains can't blow the
    Python stack.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[uuid.UUID, int] = {n: WHITE for n in nodes}

    for start in list(color):
        if color[start] != WHITE:
            continue
        # Stack frames are (node, entered?) — entered=False means "first visit".
        stack: list[tuple[uuid.UUID, bool]] = [(start, False)]
        while stack:
            node, entered = stack.pop()
            if entered:
                color[node] = BLACK
                continue
            if color[node] == GREY:
                continue
            color[node] = GREY
            stack.append((node, True))  # post-visit marker
            for nxt in adjacency.get(node, ()):
                if color.get(nxt, WHITE) == GREY:
                    raise ForkValidationError(
                        "edge DAG has a cycle (back edge "
                        f"{node} -> {nxt}); a fork must remain acyclic"
                    )
                if color.get(nxt, WHITE) == WHITE:
                    stack.append((nxt, False))


# ---------------------------------------------------------------------------
# Activation (optimistic compare-and-swap)
# ---------------------------------------------------------------------------


async def _activate(
    session: AsyncSession,
    curriculum_id: uuid.UUID,
    new_version: CurriculumVersion,
    expected_active_id: uuid.UUID,
) -> None:
    """Activate ``new_version`` via CAS on ``Curriculum.active_content_version_id``.

    Re-reads the curriculum's current pointer and compares it to the value the
    fork started from. The expectation is the *resolved* active version id:

    * if the pointer is already set (a prior fork), it must still equal the
      resolved active id;
    * if the pointer is NULL (back-filled curriculum, first fork), the resolved
      active id came from the semver bridge, and NULL is the valid pre-state.

    A mismatch means another fork won the race → ``ConcurrentForkError`` (the
    SAVEPOINT rolls back). On success we set the pointer to ``new_version`` and
    freeze its status to ``active``.
    """
    # Lock the curriculum row FOR UPDATE so concurrent activations serialize: a
    # second fork blocks here until the first commits, then re-reads the *moved*
    # pointer and fails the CAS below — instead of both reading a stale NULL
    # pointer and both activating. Closes the first-fork race on a back-filled
    # (NULL-pointer) curriculum that the bare read could not.
    curriculum = await session.scalar(
        select(Curriculum)
        .where(Curriculum.id == curriculum_id)
        .with_for_update()
    )
    current_pointer = curriculum.active_content_version_id

    # The fork is valid iff the pointer hasn't moved off the version we forked
    # from. NULL pointer ⇄ "still on the semver-bridged active id" (== expected).
    pointer_ok = (
        current_pointer == expected_active_id
        or (current_pointer is None)
    )
    if not pointer_ok:
        raise ConcurrentForkError(
            f"active pointer for curriculum {curriculum_id} moved "
            f"(expected {expected_active_id}, found {current_pointer}); retry"
        )

    curriculum.active_content_version_id = new_version.id
    new_version.status = LifecycleStatus.active
    await session.flush()
