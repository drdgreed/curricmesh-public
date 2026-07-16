"""Pure read layer over the immutable manifest (M2.0 — additive, no router yet).

This is the shared service the M2 read-path ports (graph · dashboard · diff ·
cascade/alignment) consume. It reads ONLY the new content model
(``curriculum_versions`` / ``version_members`` / ``version_edges`` /
``content_versions`` / ``lineage_assets`` — see ``app/models/content_model.py``)
and reproduces today's behavior so the ports stay golden-equivalent.

Nothing here mutates state and nothing here is wired into a router yet: it is the
manifest equivalent of the legacy graph/alignment read path, proven equal to it
by ``tests/golden/test_manifest_alignment_equiv.py``.

All queries run through the normal session, so they are org-scoped by the ambient
``current_org`` (the app-layer filter on ``TenantScoped`` selects) exactly like
every other read path. The caller is responsible for being inside the right
tenant context.

Resolving the *active* manifest
-------------------------------
The legacy source of truth for "which version is live" is
``Curriculum.current_version_id`` — a FK to a legacy ``versions`` row. The new
model has no ``current_version_id`` of its own yet (that arrives at M3 cutover),
so we bridge through **semver**, which the back-fill preserves 1:1:

    Curriculum.current_version_id
        -> legacy Version (its major/minor/patch)
        -> CurriculumVersion with that same (major, minor, patch) in this
           curriculum   [semver is unique per curriculum]

See :func:`active_curriculum_version`.

Staleness (``manifest_alignment``)
----------------------------------
``validated_against_seq`` is null on every back-filled edge (§5), so alignment
falls back to the **timestamp rule** the legacy ``alignment_report`` uses: a
dependent is stale when its selected content's ``created_at`` predates a
prerequisite's selected content's ``created_at``. We return the set of *directly*
stale dependents — matching ``alignment_report_for_version`` exactly (which the
graph endpoint folds into ``misaligned_asset_ids`` as a set, with no transitive
expansion). The precise revision-delta staleness from §3.1 (using
``validated_against_seq``) is deliberately deferred to Phase B.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.models.version import Version

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Value objects (lightweight, attribute-addressable read DTOs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestMember:
    """A version member resolved with its selected content + lineage + placement."""

    asset_id: uuid.UUID            # logical LineageAsset id
    lineage_key: str
    kind: AssetKind
    content_version_id: uuid.UUID  # the selected ContentVersion id
    content_seq: int
    content_created_at: datetime
    section: str
    week_index: int
    order: int


@dataclass(frozen=True)
class ManifestEdge:
    """A version edge on logical assets, with its (today null) provenance."""

    from_asset_id: uuid.UUID
    to_asset_id: uuid.UUID
    edge_type: str
    validated_against_seq: int | None


@dataclass(frozen=True)
class StalenessDetail:
    """A single stale dependency, with WHY it is stale (the staleness mode).

    ``mode == "revision"`` is the precise §3.1 rule: the edge carries a
    ``validated_against_seq`` and the prerequisite's currently-selected seq has
    advanced past it; ``revision_delta`` is exactly how many revisions behind the
    dependent is (``selected_seq - validated_against_seq``, always positive).

    ``mode == "timestamp"`` is the fallback used when the edge has no provenance
    (``validated_against_seq is None``): the dependent's selected content predates
    its prerequisite's selected content — the same rule ``manifest_alignment``
    uses. ``revision_delta`` is ``None`` (a timestamp comparison has no revision
    count).
    """

    dependent_asset_id: uuid.UUID    # LineageAsset id of the dependent (B)
    prerequisite_asset_id: uuid.UUID  # LineageAsset id of the prerequisite (A)
    mode: str                         # "revision" | "timestamp"
    revision_delta: int | None        # revisions behind (revision mode) else None


# ---------------------------------------------------------------------------
# Active-manifest resolution
# ---------------------------------------------------------------------------


async def active_curriculum_version(
    session: "AsyncSession", curriculum_id: uuid.UUID
) -> CurriculumVersion | None:
    """Resolve the curriculum's *active* manifest (``CurriculumVersion``).

    Resolution order:

    1. **New-model pointer** — if ``Curriculum.active_content_version_id`` is set
       (``fork()`` set it on activation), it IS the active version; return it
       directly. This is the new source of truth once a curriculum has forked.
    2. **Legacy semver bridge** — otherwise (the back-filled case, where the
       pointer is ``NULL``), bridge the legacy ``Curriculum.current_version_id``
       (a FK to a legacy ``versions`` row) to the new model by **semver**, which
       the back-fill preserves 1:1 and which is unique per curriculum:

           current_version_id -> legacy Version's (major, minor, patch)
                              -> CurriculumVersion with that semver here.

    Keeping the pointer nullable + falling back to semver is what makes M4
    backward compatible: every back-filled curriculum has a ``NULL`` pointer and
    takes path (2), so its resolved manifest — and therefore every golden-
    equivalence read — is unchanged.

    Returns ``None`` if the curriculum has no active version, the legacy version
    is missing, or no matching manifest has been back-filled yet.
    """
    curriculum = await session.scalar(
        select(Curriculum).where(Curriculum.id == curriculum_id)
    )
    if curriculum is None:
        return None

    # (1) New-model active pointer wins when present.
    if curriculum.active_content_version_id is not None:
        return await session.scalar(
            select(CurriculumVersion).where(
                CurriculumVersion.id == curriculum.active_content_version_id
            )
        )

    # (2) Fall back to the legacy semver bridge (back-filled curricula).
    if curriculum.current_version_id is None:
        return None

    legacy_version = await session.scalar(
        select(Version).where(Version.id == curriculum.current_version_id)
    )
    if legacy_version is None:
        return None

    return await session.scalar(
        select(CurriculumVersion).where(
            CurriculumVersion.curriculum_id == curriculum_id,
            CurriculumVersion.major == legacy_version.major,
            CurriculumVersion.minor == legacy_version.minor,
            CurriculumVersion.patch == legacy_version.patch,
        )
    )


# ---------------------------------------------------------------------------
# Members + edges
# ---------------------------------------------------------------------------


async def version_members(
    session: "AsyncSession", curriculum_version_id: uuid.UUID
) -> list[ManifestMember]:
    """All members of a curriculum version, each joined to its selected content
    revision and its lineage asset (kind + key), with placement.

    One row per asset present in the version. Ordered by ``(week_index, order)``
    for a deterministic read, then ``lineage_key`` as a final tiebreak.
    """
    rows = (
        await session.execute(
            select(VersionMember, ContentVersion, LineageAsset)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == curriculum_version_id)
        )
    ).all()

    members = [
        ManifestMember(
            asset_id=member.asset_id,
            lineage_key=lineage.lineage_key,
            kind=lineage.kind,
            content_version_id=content.id,
            content_seq=content.seq,
            content_created_at=content.created_at,
            section=member.section,
            week_index=member.week_index,
            order=member.order,
        )
        for member, content, lineage in rows
    ]
    members.sort(key=lambda m: (m.week_index, m.order, m.lineage_key))
    return members


async def version_edges(
    session: "AsyncSession", curriculum_version_id: uuid.UUID
) -> list[ManifestEdge]:
    """All prerequisite edges of a curriculum version (on logical asset ids)."""
    rows = (
        await session.execute(
            select(VersionEdge).where(
                VersionEdge.curriculum_version_id == curriculum_version_id
            )
        )
    ).scalars().all()

    return [
        ManifestEdge(
            from_asset_id=e.from_asset_id,
            to_asset_id=e.to_asset_id,
            edge_type=e.edge_type,
            validated_against_seq=e.validated_against_seq,
        )
        for e in rows
    ]


# ---------------------------------------------------------------------------
# Manifest-based staleness (alignment)
# ---------------------------------------------------------------------------


async def manifest_alignment(
    session: "AsyncSession", curriculum_version_id: uuid.UUID
) -> set[uuid.UUID]:
    """The manifest-based staleness report: stale dependent ``LineageAsset`` ids.

    Mirrors the legacy ``alignment_report`` over the manifest. For each edge
    ``A -> B`` (B depends on A), B is stale when B's selected content was created
    **before** A's selected content (the timestamp rule — ``validated_against_seq``
    is null on every back-filled edge today, so the revision-delta rule of §3.1 is
    not yet in play). The result is the set of *directly* stale dependents, which
    is exactly what the legacy graph endpoint surfaces as ``misaligned_asset_ids``
    (it folds ``alignment_report_for_version``'s per-edge dependents into a set —
    no transitive expansion). Equivalence is proven against that set in
    ``tests/golden/test_manifest_alignment_equiv.py``.

    Traversal is cycle-safe by construction: each edge is examined once and a
    dependent is added to a *set* (idempotent), so a cyclic prerequisite graph —
    which can occur accidentally in real curricula — cannot loop, matching the
    cascade engine's cycle-safety guarantee without needing a visited-set BFS for
    this single-pass rule.
    """
    members = await version_members(session, curriculum_version_id)
    edges = await version_edges(session, curriculum_version_id)

    # Selected-content timestamp per logical asset (the manifest's view of the
    # asset's "latest change", == legacy max(AssetVersion.created_at)).
    created_at_by_asset: dict[uuid.UUID, datetime] = {
        m.asset_id: m.content_created_at for m in members
    }

    misaligned: set[uuid.UUID] = set()
    for e in edges:
        dep_ts = created_at_by_asset.get(e.from_asset_id)       # prerequisite A
        dependent_ts = created_at_by_asset.get(e.to_asset_id)   # dependent B
        # Skip edges where either side has no selected content (graceful, like
        # the legacy alignment_report's missing-timestamp skip).
        if dep_ts is None or dependent_ts is None:
            continue
        # Stale: the dependent's content predates its prerequisite's content.
        if dependent_ts < dep_ts:
            misaligned.add(e.to_asset_id)

    return misaligned


async def manifest_alignment_detail(
    session: "AsyncSession", curriculum_version_id: uuid.UUID
) -> list[StalenessDetail]:
    """Per-edge staleness *detail* — the precise §3.1 revision-delta when known.

    The additive, detail-carrying companion to :func:`manifest_alignment`. For
    each edge ``A -> B`` (B depends on A):

    * **revision mode** — if the edge has a ``validated_against_seq`` (provenance
      captured at authoring time): resolve A's currently-selected seq ``s`` in
      this version. If ``s > validated_against_seq`` the dependent is stale by
      exactly ``s - validated_against_seq`` revisions → emit a ``"revision"``
      detail with that ``revision_delta``. If A has not advanced (``s <=
      validated_against_seq``) it is NOT stale → no emit.
    * **timestamp mode** — if the edge has no provenance (``validated_against_seq
      is None``, the back-filled case): fall back to the EXACT timestamp rule
      ``manifest_alignment`` uses (B's selected content predates A's). If stale,
      emit a ``"timestamp"`` detail with ``revision_delta=None``.

    The set of emitted ``dependent_asset_id``s is, by construction, equal to
    ``manifest_alignment`` whenever every edge is null-provenance (the back-filled
    seed) — the detail layer is a strict refinement, never a divergence, of the
    misaligned set (asserted in ``tests/staleness/``).

    Edges where either endpoint has no selected content are skipped (graceful,
    like :func:`manifest_alignment`).
    """
    members = await version_members(session, curriculum_version_id)
    edges = await version_edges(session, curriculum_version_id)

    created_at_by_asset: dict[uuid.UUID, datetime] = {
        m.asset_id: m.content_created_at for m in members
    }
    seq_by_asset: dict[uuid.UUID, int] = {
        m.asset_id: m.content_seq for m in members
    }

    details: list[StalenessDetail] = []
    for e in edges:
        # Skip edges where either side has no selected content.
        if e.from_asset_id not in created_at_by_asset:
            continue
        if e.to_asset_id not in created_at_by_asset:
            continue

        if e.validated_against_seq is not None:
            # Precise revision-delta: how far has the prerequisite advanced past
            # the seq the dependent was last validated against?
            current_seq = seq_by_asset[e.from_asset_id]
            if current_seq > e.validated_against_seq:
                details.append(
                    StalenessDetail(
                        dependent_asset_id=e.to_asset_id,
                        prerequisite_asset_id=e.from_asset_id,
                        mode="revision",
                        revision_delta=current_seq - e.validated_against_seq,
                    )
                )
            continue

        # Null provenance → timestamp fallback (same rule as manifest_alignment).
        if created_at_by_asset[e.to_asset_id] < created_at_by_asset[e.from_asset_id]:
            details.append(
                StalenessDetail(
                    dependent_asset_id=e.to_asset_id,
                    prerequisite_asset_id=e.from_asset_id,
                    mode="timestamp",
                    revision_delta=None,
                )
            )

    return details
