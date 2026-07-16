"""Change-set assembler for freshness-pipeline Phase 3 Part B.

``generate_change_set`` turns an enriched gap CCR (placement + draft frame +
dossier) into an executable ``ReleaseChangeSet`` by calling the
``ContentGenerator`` seam once per target asset, then writing the validated
result to ``ccr.change_set``.

Failure contract
----------------
Any exception raised during resolution, generation, or validation is caught,
logged as a warning, and causes the function to return ``None``.  The CCR is
left **byte-untouched** (all writes happen only after full success).  The
pipeline run continues — generation failure degrades to the Phase-2 CCR shape
(``change_set is None``).

LO rule
-------
``learning_objectives`` assets are never targeted in v1.  Any placement whose
resolved target has kind ``learning_objectives`` logs a caveat and returns
``None`` so the CCR keeps its Phase-2 shape. (One caveat: a step-7 flush
failure occurs after the in-memory writes, but it also invalidates the
transaction — the run fails and rolls back, so a partial change_set can
never persist.)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.ai.schemas import DraftFrame, Placement
from app.core.fork import Bump
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.curriculum import Curriculum
from app.models.enums import AssetKind
from app.schemas.release import ContentEditIn, NewAssetIn, ReleaseChangeSet

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ai.client import ContentGenerator
    from app.models.workflow import ChangeRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _slugify(topic: str) -> str:
    """Lower-case, replace non-alphanumeric runs with hyphens, strip leading/trailing."""
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")

def _dedupe_slug(base: str, existing_keys: set[str]) -> str:
    """Return ``base`` or the first ``base-N`` free of collisions with existing
    member lineage_keys. Bounded: >20 collisions is pathological and raises so
    the assembler degrades (a colliding key would only fail LATER at fork()
    validation — after human approval — the worst place to fail; T4 review)."""
    slug = base
    suffix = 2
    while slug in existing_keys:
        slug = f"{base}-{suffix}"
        suffix += 1
        if suffix > 20:
            raise ValueError(f"slug collision unresolvable for base {base!r}")
    return slug



async def _load_members(
    session: "AsyncSession", curriculum_version_id: object
) -> list[tuple]:
    """Return sorted ``(VersionMember, ContentVersion, LineageAsset)`` tuples.

    Order is deterministic: ``(week_index, order, lineage_key)`` — mirrors
    ``content_cards.build_content_cards``.
    """
    rows = (
        await session.execute(
            select(VersionMember, ContentVersion, LineageAsset)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == curriculum_version_id)
        )
    ).all()
    rows.sort(key=lambda r: (r[0].week_index, r[0].order, r[2].lineage_key))
    return rows


def _style_samples(
    rows: list[tuple],
    *,
    kind: AssetKind,
    target_week: int,
    exclude_lineage_key: str | None = None,
    max_samples: int = 2,
    max_chars: int = 4000,
) -> list[str]:
    """Return up to ``max_samples`` content bodies from same-kind sibling members.

    Candidates are sorted by |week_index - target_week| so the nearest siblings
    come first.  The target member itself (``exclude_lineage_key``) is skipped.
    Each body is truncated to ``max_chars``.
    """
    candidates = [
        (abs(member.week_index - target_week), content.content)
        for member, content, lineage in rows
        if lineage.kind == kind
        and content.content  # skip empty bodies
        and (exclude_lineage_key is None or lineage.lineage_key != exclude_lineage_key)
    ]
    candidates.sort(key=lambda x: x[0])
    return [body[:max_chars] for _, body in candidates[:max_samples]]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_change_set(
    session: "AsyncSession",
    *,
    ccr: "ChangeRequest",
    generator: "ContentGenerator",
) -> ReleaseChangeSet | None:
    """Assemble an executable ``ReleaseChangeSet`` for an enriched gap CCR.

    Implements the 8-step algorithm from the Phase-3 execute plan:

    1. Read ``ccr.impact`` — validate enrichment presence.
    2. Load curriculum members from the active immutable version.
    3. Resolve targets by ``placement.target_kind``.
    4. Build style samples (up to 2 same-kind siblings, ≤4 000 chars each).
    5. Call ``generator.generate_asset_content`` per target.
    6. Build and validate ``ReleaseChangeSet``.
    7. Write ``ccr.change_set`` + ``impact["generation"]``; flush.
    8. Any exception → log warning, return ``None``, CCR untouched.

    Returns the validated ``ReleaseChangeSet`` on success, ``None`` on failure.
    Flushes; never commits.
    """
    # ------------------------------------------------------------------
    # Step 1: validate impact keys before entering the protected zone.
    # ------------------------------------------------------------------
    impact = ccr.impact or {}
    enrichment = impact.get("enrichment")
    if not enrichment:
        logger.warning(
            "generate_change_set: CCR %s has no enrichment in impact — degrading to Phase-2 shape",
            ccr.id,
        )
        return None

    placement_dict = enrichment.get("placement")
    draft_frame_dict = enrichment.get("draft_frame")
    if not placement_dict or not draft_frame_dict:
        logger.warning(
            "generate_change_set: CCR %s missing placement or draft_frame in enrichment — degrading",
            ccr.id,
        )
        return None

    ai_research = impact.get("ai_research") or {}
    topic: str = ai_research.get("topic") or ""
    dossier: list[dict] = impact.get("dossier") or []
    if not dossier:
        logger.warning(
            "generate_change_set: CCR %s has no dossier (enriched but never judged?) "
            "— generating with gap evidence only",
            ccr.id,
        )

    # ------------------------------------------------------------------
    # Steps 2-7: wrapped — any exception leaves CCR untouched.
    # ------------------------------------------------------------------
    try:
        placement = Placement.model_validate(placement_dict)
        draft_frame = DraftFrame.model_validate(draft_frame_dict)

        # Step 2: resolve active curriculum version + members.
        curriculum = (
            await session.execute(
                select(Curriculum).where(Curriculum.id == ccr.curriculum_id)
            )
        ).scalar_one_or_none()

        if curriculum is None or curriculum.active_content_version_id is None:
            logger.warning(
                "generate_change_set: CCR %s — curriculum %s has no active content version",
                ccr.id,
                ccr.curriculum_id,
            )
            return None

        rows = await _load_members(session, curriculum.active_content_version_id)

        existing_keys = {lineage.lineage_key for _, _, lineage in rows}
        max_week = max((member.week_index for member, _, _ in rows), default=0)

        # Step 3: resolve targets.
        changed: list[ContentEditIn] = []
        added: list[NewAssetIn] = []
        summaries: dict[str, str] = {}
        caveats: list[str] = []
        targets: list[str] = []

        target_kind = placement.target_kind
        target_ref = placement.target_ref

        # Slug for new assets (computed once; collision check inside each branch).
        slug_base = _slugify(topic) if topic else "new-content"

        # ----------------------------------------------------------------
        # modify_asset
        # ----------------------------------------------------------------
        if target_kind == "modify_asset":
            matched = next(
                (
                    (m, c, la)
                    for m, c, la in rows
                    if la.lineage_key == target_ref
                ),
                None,
            )
            if matched is None:
                logger.warning(
                    "generate_change_set: CCR %s modify_asset target_ref %r not found",
                    ccr.id,
                    target_ref,
                )
                return None

            member, content, lineage = matched

            if lineage.kind == AssetKind.learning_objectives:
                logger.warning(
                    "generate_change_set: CCR %s targets learning_objectives asset %r "
                    "— v1 LO-skip rule; degrading to Phase-2 shape",
                    ccr.id,
                    target_ref,
                )
                return None

            samples = _style_samples(
                rows,
                kind=lineage.kind,
                target_week=member.week_index,
                exclude_lineage_key=lineage.lineage_key,
            )
            gen = await generator.generate_asset_content(
                mode="edit",
                current_content=content.content,
                draft_frame=draft_frame.model_dump(),
                dossier=dossier,
                style_samples=samples,
                asset_kind=lineage.kind.value,
                topic=topic,
            )
            changed.append(ContentEditIn(lineage_key=lineage.lineage_key, content=gen.content))
            summaries[lineage.lineage_key] = gen.summary_of_changes
            caveats.extend(gen.caveats)
            targets.append(lineage.lineage_key)

        # ----------------------------------------------------------------
        # modify_module
        # ----------------------------------------------------------------
        elif target_kind == "modify_module":
            try:
                week_int = int(target_ref)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                logger.warning(
                    "generate_change_set: CCR %s modify_module target_ref %r is not a valid int",
                    ccr.id,
                    target_ref,
                )
                return None

            module_members = [
                (m, c, la)
                for m, c, la in rows
                if m.week_index == week_int and la.kind == AssetKind.lesson_plan
            ]
            if not module_members:
                logger.warning(
                    "generate_change_set: CCR %s modify_module — no lesson_plan members at week %d",
                    ccr.id,
                    week_int,
                )
                return None

            for member, content, lineage in module_members:
                samples = _style_samples(
                    rows,
                    kind=AssetKind.lesson_plan,
                    target_week=member.week_index,
                    exclude_lineage_key=lineage.lineage_key,
                )
                gen = await generator.generate_asset_content(
                    mode="edit",
                    current_content=content.content,
                    draft_frame=draft_frame.model_dump(),
                    dossier=dossier,
                    style_samples=samples,
                    asset_kind=lineage.kind.value,
                    topic=topic,
                )
                changed.append(ContentEditIn(lineage_key=lineage.lineage_key, content=gen.content))
                summaries[lineage.lineage_key] = gen.summary_of_changes
                caveats.extend(gen.caveats)
                targets.append(lineage.lineage_key)

        # ----------------------------------------------------------------
        # new_module
        # ----------------------------------------------------------------
        elif target_kind == "new_module":
            new_week = max_week + 1
            section = topic  # display topic from impact.ai_research.topic

            # Lesson-plan slug with collision check.
            lp_slug = _dedupe_slug(slug_base, existing_keys)

            samples_lp = _style_samples(
                rows, kind=AssetKind.lesson_plan, target_week=new_week
            )
            gen_lp = await generator.generate_asset_content(
                mode="new",
                current_content=None,
                draft_frame=draft_frame.model_dump(),
                dossier=dossier,
                style_samples=samples_lp,
                asset_kind=AssetKind.lesson_plan.value,
                topic=topic,
            )
            added.append(NewAssetIn(
                lineage_key=lp_slug,
                kind=AssetKind.lesson_plan,
                content=gen_lp.content,
                metadata=None,
                section=section,
                week_index=new_week,
                order=0,
            ))
            summaries[lp_slug] = gen_lp.summary_of_changes
            caveats.extend(gen_lp.caveats)
            targets.append(lp_slug)

            # Assessment — only when draft_frame has sample assessments.
            if draft_frame.sample_assessments:
                assess_base = f"{lp_slug}-assessment"
                assess_slug = (
                    _dedupe_slug(assess_base, existing_keys)
                )
                samples_assess = _style_samples(
                    rows, kind=AssetKind.assessment, target_week=new_week
                )
                gen_assess = await generator.generate_asset_content(
                    mode="new",
                    current_content=None,
                    draft_frame=draft_frame.model_dump(),
                    dossier=dossier,
                    style_samples=samples_assess,
                    asset_kind=AssetKind.assessment.value,
                    topic=topic,
                )
                added.append(NewAssetIn(
                    lineage_key=assess_slug,
                    kind=AssetKind.assessment,
                    content=gen_assess.content,
                    metadata=None,
                    section=section,
                    week_index=new_week,
                    order=1,
                ))
                summaries[assess_slug] = gen_assess.summary_of_changes
                caveats.extend(gen_assess.caveats)
                targets.append(assess_slug)

        # ----------------------------------------------------------------
        # new_asset
        # ----------------------------------------------------------------
        elif target_kind == "new_asset":
            # Resolve target module's section/week from target_ref (module index).
            module_week: int | None = None
            try:
                module_week = int(target_ref)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                module_week = None

            if module_week is not None:
                module_match = next(
                    (m for m, _, _ in rows if m.week_index == module_week),
                    None,
                )
                if module_match is None:
                    module_week = None  # fallback to max+1

            if module_week is not None:
                week_for_asset = module_week
                # Use the section from the first member in that week.
                section = next(
                    m.section for m, _, _ in rows if m.week_index == module_week
                )
            else:
                week_for_asset = max_week + 1
                section = topic

            asset_slug = _dedupe_slug(slug_base, existing_keys)

            samples = _style_samples(
                rows, kind=AssetKind.lesson_plan, target_week=week_for_asset
            )
            gen = await generator.generate_asset_content(
                mode="new",
                current_content=None,
                draft_frame=draft_frame.model_dump(),
                dossier=dossier,
                style_samples=samples,
                asset_kind=AssetKind.lesson_plan.value,
                topic=topic,
            )
            added.append(NewAssetIn(
                lineage_key=asset_slug,
                kind=AssetKind.lesson_plan,
                content=gen.content,
                metadata=None,
                section=section,
                week_index=week_for_asset,
                order=0,
            ))
            summaries[asset_slug] = gen.summary_of_changes
            caveats.extend(gen.caveats)
            targets.append(asset_slug)

        else:
            logger.warning(
                "generate_change_set: CCR %s unknown target_kind %r — degrading",
                ccr.id,
                target_kind,
            )
            return None

        # Step 6: build + validate ReleaseChangeSet.
        bump = Bump(ccr.proposed_bump or "minor")
        cs = ReleaseChangeSet(bump=bump, changed=changed, added=added)
        # Round-trip validation (catches any field-level issues before writing).
        ReleaseChangeSet.model_validate(cs.model_dump(mode="json"))

        # Step 7: write to CCR — all mutations happen HERE, after full success.
        new_impact = dict(ccr.impact or {})
        new_impact["generation"] = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "targets": targets,
            "summaries": summaries,
            "caveats": caveats,
        }
        ccr.change_set = cs.model_dump(mode="json")
        ccr.impact = new_impact
        await session.flush()

        return cs

    except Exception as exc:
        logger.warning(
            "generate_change_set: CCR %s generation failed: %s",
            ccr.id,
            exc,
            exc_info=True,
        )
        return None
