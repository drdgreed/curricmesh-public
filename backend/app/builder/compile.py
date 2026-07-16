"""Publish/compile — turn a mutable ``DraftCourse`` into the immutable model (Task 7).

This is the Course Builder's *publish* operation: it materializes a freely-edited
draft (``app/builder/models.py``) into CurricMesh's content-addressed version model
(``app/models/content_model.py``) so the rest of the system — graph, calendar,
alignment, review/merge — can manage it like any other curriculum.

It mirrors the construction patterns of :mod:`app.migration.backfill_content_model`
(build ``LineageAsset`` + ``ContentVersion`` + ``CurriculumVersion`` +
``VersionMember`` + ``VersionEdge`` from source rows) and reuses
:func:`app.core.fork._validate` — the *same* acyclicity + referential validator a
fork runs — so a published curriculum is structurally indistinguishable from a
forked/back-filled one and every existing read path (manifest · graph · calendar ·
alignment) accepts it unchanged.

Transaction contract (matches ``fork()`` / ``releases.py``)
-----------------------------------------------------------
Everything runs inside ONE ``SAVEPOINT`` (``session.begin_nested``): any failure
rolls back the whole compile — no half-written curriculum, the draft untouched —
which is fail-closed. The **caller owns the outer commit** (the router commits on
success), exactly like ``fork()``.

What a publish produces
-----------------------
* a new ``Curriculum`` (name from the draft title, a slug derived from it,
  ``active_content_version_id`` pointed at the new version);
* a single ``CurriculumVersion`` v1.0.0, ``status = active``, ``parent = None``
  (a published draft starts a fresh lineage — subsequent edits go through
  ``fork()`` as releases);
* one ``LineageAsset`` + one ``ContentVersion`` (``seq = 1``) per ``DraftItem``
  and per ``DraftObjective``;
* a ``VersionMember`` for every item AND every objective (placement from the
  draft's week/order);
* a ``VersionEdge`` per ``DraftDependency`` (item → item, carrying its
  ``edge_type``) and a ``supports`` edge per ``DraftItemObjective`` (item →
  objective). All edges reference the *logical* lineage assets, per the model.

Tenant scoping is ambient: every new row is ``TenantScoped`` and write-stamps
``organization_id`` from the active ``current_org`` (the caller must already be
inside the right tenant context — same contract as every other write path).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.content_hash import content_hash
from app.core.fork import ForkValidationError, _validate
from app.core.history import EventType, record
from app.models.content_model import (
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind, LifecycleStatus
from app.builder.models import (
    DraftCourse,
    DraftDependency,
    DraftItem,
    DraftItemMedia,
    DraftItemObjective,
    DraftObjective,
)
from app.models.media import MediaAsset
from app.models.workflow import ChangeRequest


# ---------------------------------------------------------------------------
# Errors (module-local, mirroring ForkError / WorkflowError convention)
# ---------------------------------------------------------------------------


class CompileError(Exception):
    """Base class for every publish/compile failure."""


class DraftNotFoundError(CompileError):
    """The draft course to publish does not exist (router maps to 404)."""


class AlreadyPublishedError(CompileError):
    """The draft has already been published (router maps to 409)."""


# A compile that produces an invalid manifest (cycle / dangling edge / bad
# placement) is a ``ForkValidationError`` — the SAME error fork's ``_validate``
# raises — so the router maps it to 422 exactly like a release. Re-exported here
# so callers import one place.
CompileValidationError = ForkValidationError


# ---------------------------------------------------------------------------
# Initial-release binding (slice 5 — mandatory QA -> release)
# ---------------------------------------------------------------------------

# The key under a ChangeRequest.impact JSONB that marks it an "initial release"
# CCR and pins the pre-active candidate CurriculumVersion it will activate. We
# store this on `impact` (a metadata bag) — NOT `change_set` — so the CCR keeps
# change_set == None. That means the fork-replay /merge path naturally rejects an
# initial-release CCR ("no executable change-set", 400): a first course has no
# parent to fork against, so the ONLY activation path is the gated
# `activate_initial_release` (see app/core/workflow/engine.py).
INITIAL_RELEASE_KEY = "initial_release"


def initial_release_marker(ccr: ChangeRequest) -> dict | None:
    """Return the initial-release marker on a CCR, or None if it isn't one."""
    marker = (ccr.impact or {}).get(INITIAL_RELEASE_KEY)
    return marker if isinstance(marker, dict) else None


@dataclass(frozen=True)
class PublishResult:
    """What a publish (submit-for-review) produces.

    A pre-active candidate ``CurriculumVersion`` (status ``review``, NOT active)
    plus the ``ChangeRequest`` that gates its activation through the standard
    6-dimension QA + approval engine. The curriculum has NO active version yet —
    ``active_content_version_id`` is set only when the gate clears.
    """

    version: CurriculumVersion
    ccr: ChangeRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """A url-safe slug derived from a title (lowercase, hyphenated, ascii)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "course"


async def _unique_slug(session: AsyncSession, base: str) -> str:
    """A slug unique across ``curricula`` (``slug`` is globally unique).

    Appends a short random suffix on collision rather than a counter so two
    concurrent publishes of same-titled drafts don't both pick ``-2``.
    """
    candidate = base
    while (
        await session.scalar(select(Curriculum.id).where(Curriculum.slug == candidate))
    ) is not None:
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def _key_skills(objective: DraftObjective) -> list:
    """Unwrap the ``{"skills": [...]}`` JSONB envelope to a flat list."""
    raw = objective.key_skills or {}
    skills = raw.get("skills") if isinstance(raw, dict) else None
    return skills or []


# In-content embed convention (documented in docs/MEDIA_SUBSYSTEM.md): an author
# references an owned asset inline as ``![[media:{media_asset_id}]]``.
_MEDIA_REF_RE = re.compile(r"!\[\[media:([0-9a-fA-F-]{36})\]\]")


def _asset_ref(asset: MediaAsset, order_index: int) -> dict:
    """A frozen snapshot of a media asset, pinned into the immutable version.

    Captures everything a renderer needs (id + storage_key + kind + filename +
    mime + duration) so the released version renders the asset it shipped with
    even if the draft link or the asset row later changes.
    """
    return {
        "media_asset_id": str(asset.id),
        "storage_key": asset.storage_key,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime": asset.mime,
        "duration_s": asset.duration_s,
        "order_index": order_index,
    }


async def _gather_media_refs(
    session: AsyncSession, item: DraftItem
) -> list[dict] | None:
    """Freeze the media assets a draft item references, for the immutable model.

    Sources, unioned (an asset appears at most once):
      1. Explicit ``DraftItemMedia`` attachments (authoritative), in
         ``order_index`` order.
      2. Inline ``![[media:{id}]]`` embeds in the item content that were not
         formally attached — pinned defensively so a rendered embed never
         dangles. A ref that resolves to no in-org asset is skipped (we cannot
         pin what the tenant does not own).

    Returns the ref list, or ``None`` when the item references no media.
    """
    rows = (
        await session.execute(
            select(DraftItemMedia, MediaAsset)
            .join(MediaAsset, MediaAsset.id == DraftItemMedia.media_asset_id)
            .where(DraftItemMedia.draft_item_id == item.id)
            .order_by(DraftItemMedia.order_index, DraftItemMedia.created_at)
        )
    ).all()

    refs: list[dict] = []
    seen: set[uuid.UUID] = set()
    for link, asset in rows:
        refs.append(_asset_ref(asset, link.order_index))
        seen.add(asset.id)

    for match in _MEDIA_REF_RE.finditer(item.content or ""):
        try:
            asset_id = uuid.UUID(match.group(1))
        except ValueError:
            continue
        if asset_id in seen:
            continue
        asset = (
            await session.execute(
                select(MediaAsset).where(MediaAsset.id == asset_id)
            )
        ).scalar_one_or_none()
        if asset is None:
            continue  # dangling / cross-org embed — nothing to pin
        refs.append(_asset_ref(asset, len(refs)))
        seen.add(asset_id)

    return refs or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def publish_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    author_id: uuid.UUID | None = None,
) -> PublishResult:
    """Submit a ``DraftCourse`` for review: assemble a pre-active candidate version.

    Slice 5 (mandatory QA -> release): a publish NO LONGER goes straight to
    active. It assembles the COMPLETE immutable ``CurriculumVersion`` exactly as
    before, but with a **candidate** status (``review``) and WITHOUT setting the
    curriculum's ``active_content_version_id``. It also opens an **initial-release
    ``ChangeRequest``** so the existing 6-dimension QA + approval engine can gate
    activation (see :func:`app.core.workflow.engine.activate_initial_release`).
    An un-QA'd course therefore cannot reach ``active`` by this path.

    Runs inside one SAVEPOINT (fail-closed). The caller owns the outer commit.

    Args:
        session:   Active AsyncSession (already inside the right tenant context).
        draft_id:  The draft course to submit.
        author_id: Optional FK to the submitting user (stamped on the CCR).

    Raises:
        DraftNotFoundError: no draft with ``draft_id`` (router → 404).
        AlreadyPublishedError: the draft was already submitted/published
            (router → 409).
        CompileValidationError (== ForkValidationError): the assembled manifest is
            invalid — a prerequisite cycle or a dangling edge (router → 422). The
            SAVEPOINT rolls back; nothing is persisted.

    Returns a :class:`PublishResult` (candidate version + its initial-release CCR).
    """
    async with session.begin_nested():
        draft = await session.scalar(
            select(DraftCourse).where(DraftCourse.id == draft_id)
        )
        if draft is None:
            raise DraftNotFoundError(f"draft course {draft_id} not found")
        if draft.status in ("in_review", "published"):
            raise AlreadyPublishedError(
                f"draft course {draft_id} has already been submitted for review"
            )

        # --- 1. Curriculum + its v1.0.0 CANDIDATE CurriculumVersion. ---
        # status = review (pre-active); the curriculum's active pointer stays NULL
        # until the QA/approval gate clears in activate_initial_release().
        base_slug = _slugify(draft.title)
        slug = await _unique_slug(session, base_slug)
        curriculum = Curriculum(name=draft.title, slug=slug)
        session.add(curriculum)
        await session.flush()

        new_version = CurriculumVersion(
            curriculum_id=curriculum.id,
            major=1,
            minor=0,
            patch=0,
            status=LifecycleStatus.review,
            parent_version_id=None,
        )
        session.add(new_version)
        await session.flush()

        # --- 2. Load the draft's source rows. ---
        items = (
            (
                await session.execute(
                    select(DraftItem).where(DraftItem.draft_course_id == draft_id)
                )
            )
            .scalars()
            .all()
        )
        objectives = (
            (
                await session.execute(
                    select(DraftObjective).where(
                        DraftObjective.draft_course_id == draft_id
                    )
                )
            )
            .scalars()
            .all()
        )

        # --- 3. LineageAsset + ContentVersion + member per item. ---
        # draft_item_id -> LineageAsset (for edge endpoints + supports edges).
        lineage_by_item: dict[uuid.UUID, LineageAsset] = {}
        used_keys: set[str] = set()

        for item in items:
            week = item.week_index if item.week_index is not None else 0
            lineage_key = _unique_key(
                used_keys,
                f"{slug}/v1/wk{week}/{item.kind.value}/{_short(item.id)}",
            )
            lineage = LineageAsset(
                kind=item.kind,
                lineage_key=lineage_key,
                source_url=item.source_url,
            )
            session.add(lineage)
            await session.flush()
            lineage_by_item[item.id] = lineage

            metadata = {
                "metrics": item.metrics or {},
                "estimated_minutes": item.estimated_minutes,
                "week_index": item.week_index,
                "order_index": item.order_index,
            }
            # Carry the rubric into the immutable release so the
            # assessment-feedback tutor (B5) can find it at
            # ContentVersion.metadata_["rubric"]. Only written when present
            # (never null/empty) to keep non-assessment item metadata clean.
            if item.ai_notes and isinstance(item.ai_notes, dict):
                rubric = item.ai_notes.get("rubric")
                if rubric:
                    metadata["rubric"] = rubric
            # Freeze the item's referenced media into the immutable version so
            # the release pins the exact assets it shipped with (slice 2).
            media_refs = await _gather_media_refs(session, item)
            cv = ContentVersion(
                asset_id=lineage.id,
                seq=1,
                content=item.content or "",
                metadata_=metadata,
                media_refs=media_refs,
                content_hash=content_hash(
                    item.kind.value, item.content or "", metadata
                ),
                created_by=None,
            )
            session.add(cv)
            await session.flush()

            session.add(
                VersionMember(
                    curriculum_version_id=new_version.id,
                    asset_id=lineage.id,
                    asset_version_id=cv.id,
                    section=f"Week {week}",
                    week_index=week,
                    order=item.order_index,
                )
            )

        # --- 4. LineageAsset + ContentVersion + member per objective. ---
        lineage_by_objective: dict[uuid.UUID, LineageAsset] = {}
        for n, obj in enumerate(objectives, start=1):
            week = obj.week_index if obj.week_index is not None else 0
            lineage_key = _unique_key(used_keys, f"{slug}/v1/obj/{n}")
            lineage = LineageAsset(
                kind=AssetKind.learning_objectives,
                lineage_key=lineage_key,
                source_url=None,
            )
            session.add(lineage)
            await session.flush()
            lineage_by_objective[obj.id] = lineage

            metadata = {
                "bloom_level": obj.bloom_level,
                "key_skills": _key_skills(obj),
                "week_index": obj.week_index,
            }
            cv = ContentVersion(
                asset_id=lineage.id,
                seq=1,
                content=obj.text or "",
                metadata_=metadata,
                content_hash=content_hash(
                    AssetKind.learning_objectives.value, obj.text or "", metadata
                ),
                created_by=None,
            )
            session.add(cv)
            await session.flush()

            session.add(
                VersionMember(
                    curriculum_version_id=new_version.id,
                    asset_id=lineage.id,
                    asset_version_id=cv.id,
                    section=f"Week {week}",
                    week_index=week,
                    order=obj.order_index,
                )
            )

        # --- 5. Edges: dependencies (item->item) + supports (item->objective). ---
        # Only ACCEPTED edges become VersionEdges — ai_suggested rows with
        # accepted=False must NOT be promoted into the immutable version.
        deps = (
            (
                await session.execute(
                    select(DraftDependency).where(
                        DraftDependency.draft_course_id == draft_id,
                        DraftDependency.accepted == True,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        for dep in deps:
            from_lineage = lineage_by_item.get(dep.from_item_id)
            to_lineage = lineage_by_item.get(dep.to_item_id)
            if from_lineage is None or to_lineage is None:
                # An endpoint isn't an item of this course — skip defensively
                # (the FK + course scope make this unreachable in practice).
                continue
            session.add(
                VersionEdge(
                    curriculum_version_id=new_version.id,
                    from_asset_id=from_lineage.id,
                    to_asset_id=to_lineage.id,
                    edge_type=dep.edge_type,
                    validated_against_seq=None,
                )
            )

        alignments = (
            (
                await session.execute(
                    select(DraftItemObjective).where(
                        DraftItemObjective.draft_item_id.in_(
                            [i.id for i in items]
                        )
                    )
                )
            )
            .scalars()
            .all()
        ) if items else []
        for link in alignments:
            item_lineage = lineage_by_item.get(link.draft_item_id)
            obj_lineage = lineage_by_objective.get(link.draft_objective_id)
            if item_lineage is None or obj_lineage is None:
                continue
            session.add(
                VersionEdge(
                    curriculum_version_id=new_version.id,
                    from_asset_id=item_lineage.id,
                    to_asset_id=obj_lineage.id,
                    edge_type="supports",
                    validated_against_seq=None,
                )
            )

        await session.flush()

        # --- 6. Validate (the SAME acyclicity + referential check fork runs). ---
        # Acyclicity is asserted over ALL edges (prerequisite + supports); since
        # supports edges only point item -> objective and objectives are sinks,
        # they never introduce a cycle, so this matches the prerequisite-DAG
        # acyclicity contract while still catching a cyclic DraftDependency.
        await _validate(session, new_version.id)

        # --- 7. Open the initial-release CCR + link the draft (NO activation). ---
        # The candidate version is fully assembled but NOT active; the curriculum's
        # active pointer is deliberately left NULL. Activation happens only when
        # activate_initial_release() clears the QA + approval gate on this CCR.
        ccr = ChangeRequest(
            curriculum_id=curriculum.id,
            author_id=author_id,
            title=f"[Initial Release] {draft.title}",
            rationale=(
                "Initial release of an authored course. Must clear the "
                "6-dimension QA + approval gate before it becomes active."
            ),
            proposed_bump="major",
            status=LifecycleStatus.draft,
            impact={
                INITIAL_RELEASE_KEY: {"candidate_version_id": str(new_version.id)}
            },
        )
        session.add(ccr)
        await session.flush()

        await record(
            session,
            actor_id=author_id,
            event_type=EventType.ccr_created,
            target=f"ccr:{ccr.id}",
            details={
                "curriculum_id": str(curriculum.id),
                "candidate_version_id": str(new_version.id),
                "initial_release": True,
                "title": ccr.title,
            },
        )

        draft.curriculum_id = curriculum.id
        draft.status = "in_review"
        await session.flush()

    await session.refresh(new_version)
    await session.refresh(ccr)
    return PublishResult(version=new_version, ccr=ccr)


def _short(id_: uuid.UUID) -> str:
    """A short, stable id fragment for a lineage key."""
    return id_.hex[:8]


def _unique_key(used: set[str], candidate: str) -> str:
    """Ensure ``candidate`` is unique within this course's lineage keys."""
    key = candidate
    n = 2
    while key in used:
        key = f"{candidate}-{n}"
        n += 1
    used.add(key)
    return key
