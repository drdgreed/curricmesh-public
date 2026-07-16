"""Version-diff service for CurricMesh — Task B4.

Architecture:
    Pure diff core  — text_diff(), rubric_diff(), lo_diff(), diff()
    DB-backed wrapper — diff_versions() (thin async layer)

Content convention:
    Text-ish kinds (lesson_plan, slides, spec, lab, references, starter):
        body_ref is markdown/plain text.  Diffed via text_diff().

    Structured kinds (rubric, learning_objectives, assessment*):
        body_ref is a JSON string that is parsed before diffing.
        *assessment is treated as text (no domain-specific schema) unless
        that schema is specified later.  Only rubric and learning_objectives
        have dedicated structured differs.

JSON shape assumptions (must match callers):
    Rubric:  {"criteria": [{"name": str, "weight": float}, ...]}
             "name" is the unique key for matching criteria across versions.

    Learning-objectives: [{"id": str, "text": str}, ...]
             "id" is the unique key for matching items across versions.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Accept any object that exposes .asset_id, .body_ref, and .asset.kind.value.
    # This keeps the pure core free of SQLAlchemy imports.
    from types import SimpleNamespace


class DiffError(ValueError):
    """Raised when a structured diff cannot be performed due to bad content.

    Distinct from plain ValueError (which is used for not-found / cross-asset
    membership failures).  The router maps DiffError → HTTP 422.
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TextDiff:
    """Result of a line-based unified diff on two text strings.

    Attributes:
        added:   Lines present in *b* but not *a* (content only, no leading +).
        removed: Lines present in *a* but not *b* (content only, no leading -).
        unified: Full unified-diff string (with +/- markers and @@ headers).
    """

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unified: str = ""


@dataclass
class StructuredDiff:
    """Result of a semantic diff on a structured document (rubric, LOs).

    Attributes:
        added:   Items present in *b* but not *a* (full item dicts).
        removed: Items present in *a* but not *b* (full item dicts).
        changed: Modifications to existing items: [{"key": str, "from": Any, "to": Any}, ...].
                 For rubric: key=criterion name, from/to=weight values.
                 For LOs:    key=LO id,          from/to=text values.
    """

    added: list[Any] = field(default_factory=list)
    removed: list[Any] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DiffResult:
    """Top-level diff result combining kind metadata with the actual diff payload.

    Exactly one of *text* or *structured* is non-None, depending on the kind.

    Attributes:
        kind:       The AssetKind string of the diffed asset (e.g. "rubric").
        text:       Populated for text-ish kinds; None for structured kinds.
        structured: Populated for structured kinds; None for text kinds.
    """

    kind: str
    text: TextDiff | None = None
    structured: StructuredDiff | None = None


# ---------------------------------------------------------------------------
# Pure diff functions
# ---------------------------------------------------------------------------

_TEXT_KINDS = frozenset({
    "lesson_plan", "slides", "spec", "lab", "references", "starter",
})
_STRUCTURED_KINDS = frozenset({"rubric", "learning_objectives"})


def text_diff(a: str, b: str) -> TextDiff:
    """Produce a unified line diff of two text strings.

    Args:
        a: The "before" text (may be empty string).
        b: The "after" text (may be empty string).

    Returns:
        TextDiff with:
          - added:   non-empty lines added in b (the leading '+' diff marker is
                     stripped, but line content is otherwise preserved as-is).
          - removed: non-empty lines removed from a (the leading '-' marker is
                     stripped; line content is otherwise preserved as-is).
          - unified: full unified-diff string including @@ headers and +/- markers.
    """
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(a_lines, b_lines, fromfile="a", tofile="b")
    )

    unified = "".join(diff_lines)

    added: list[str] = []
    removed: list[str] = []

    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            content = line[1:].rstrip("\n")
            if content:
                added.append(content)
        elif line.startswith("-"):
            content = line[1:].rstrip("\n")
            if content:
                removed.append(content)

    return TextDiff(added=added, removed=removed, unified=unified)


def rubric_diff(a: dict, b: dict) -> StructuredDiff:
    """Semantic diff of two rubric dicts.

    Expected shape: {"criteria": [{"name": str, "weight": float}, ...]}
    "name" is the unique key; criteria are matched by name across versions.

    Args:
        a: The "before" rubric dict.
        b: The "after" rubric dict.

    Returns:
        StructuredDiff where:
          - added:   criteria in b not in a (full {"name", "weight"} dicts).
          - removed: criteria in a not in b.
          - changed: [{"key": name, "from": old_weight, "to": new_weight}, ...]
                     only for criteria whose weight changed.
    """
    a_map: dict[str, float] = {c["name"]: c["weight"] for c in a.get("criteria", [])}
    b_map: dict[str, float] = {c["name"]: c["weight"] for c in b.get("criteria", [])}

    added = [{"name": name, "weight": b_map[name]} for name in b_map if name not in a_map]
    removed = [{"name": name, "weight": a_map[name]} for name in a_map if name not in b_map]
    changed = [
        {"key": name, "from": a_map[name], "to": b_map[name]}
        for name in a_map
        if name in b_map and abs(a_map[name] - b_map[name]) > 1e-9
    ]

    return StructuredDiff(added=added, removed=removed, changed=changed)


def lo_diff(a: list, b: list) -> StructuredDiff:
    """Semantic diff of two learning-objectives lists.

    Expected shape: [{"id": str, "text": str}, ...]
    "id" is the unique key; items are matched by id across versions.

    Args:
        a: The "before" LO list.
        b: The "after" LO list.

    Returns:
        StructuredDiff where:
          - added:   items in b not in a (full {"id", "text"} dicts).
          - removed: items in a not in b.
          - changed: [{"key": id, "from": old_text, "to": new_text}, ...]
                     only for items whose text changed.
    """
    a_map: dict[str, str] = {item["id"]: item["text"] for item in a}
    b_map: dict[str, str] = {item["id"]: item["text"] for item in b}

    added = [{"id": lo_id, "text": b_map[lo_id]} for lo_id in b_map if lo_id not in a_map]
    removed = [{"id": lo_id, "text": a_map[lo_id]} for lo_id in a_map if lo_id not in b_map]
    changed = [
        {"key": lo_id, "from": a_map[lo_id], "to": b_map[lo_id]}
        for lo_id in a_map
        if lo_id in b_map and a_map[lo_id] != b_map[lo_id]
    ]

    return StructuredDiff(added=added, removed=removed, changed=changed)


# ---------------------------------------------------------------------------
# Dispatcher — accepts AssetVersion-like objects
# ---------------------------------------------------------------------------


def diff(a: "SimpleNamespace", b: "SimpleNamespace") -> DiffResult:
    """Diff two AssetVersion-like objects and return a DiffResult.

    The two versions MUST belong to the same Asset.  The asset's kind
    determines which differ is called.

    The objects must expose:
        .asset_id          — UUID of the owning asset
        .body_ref          — str | None — the version content
        .asset.kind.value  — str — the AssetKind (e.g. "rubric")

    Args:
        a: The "before" AssetVersion (or compatible SimpleNamespace).
        b: The "after" AssetVersion (or compatible SimpleNamespace).

    Returns:
        DiffResult populated with either .text or .structured.

    Raises:
        ValueError: if a.asset_id != b.asset_id (cross-asset diff rejected).
    """
    if a.asset_id != b.asset_id:
        raise ValueError(
            f"Cannot diff versions from different assets: "
            f"{a.asset_id!r} vs {b.asset_id!r}"
        )

    kind: str = a.asset.kind.value
    body_a: str = a.body_ref or ""
    body_b: str = b.body_ref or ""

    if kind in _STRUCTURED_KINDS:
        try:
            if kind == "rubric":
                parsed_a = json.loads(body_a) if body_a else {"criteria": []}
                parsed_b = json.loads(body_b) if body_b else {"criteria": []}
                return DiffResult(kind=kind, structured=rubric_diff(parsed_a, parsed_b))
            if kind == "learning_objectives":
                parsed_a = json.loads(body_a) if body_a else []
                parsed_b = json.loads(body_b) if body_b else []
                return DiffResult(kind=kind, structured=lo_diff(parsed_a, parsed_b))
        except json.JSONDecodeError as exc:
            raise DiffError("asset version body is not valid JSON") from exc

    # All remaining kinds (text kinds + unknown structured without a specific differ) → text diff
    return DiffResult(kind=kind, text=text_diff(body_a, body_b))


# ---------------------------------------------------------------------------
# DB-backed wrapper (thin async layer) — immutable-content read path + fallback
# ---------------------------------------------------------------------------
#
# Strangler read-path port (M2): when the asset has been back-filled into the
# immutable content model, the diff reads the two ``ContentVersion`` bodies and
# dispatches by kind exactly as the legacy path does. When no content-version
# chain exists for the asset, it FALLS BACK to the legacy ``AssetVersion.body_ref``
# path so existing diff fixtures (old-model only) stay green.
#
# Bridge from a legacy AssetVersion to its immutable ContentVersion:
# the back-fill (``app.migration.backfill_content_model``) created one
# ContentVersion per legacy AssetVersion, deduped within a lineage by
# ``content_hash(kind, body_ref, metadata)``. So we recompute that same hash for
# the requested AssetVersion and look it up on the lineage — exactly mirroring
# how the back-fill addressed content. This is robust to the back-fill's
# content-dedup (two identical AssetVersions share one ContentVersion) and to
# ``seq`` reassignment, because the *content* is the address, not the position.


async def diff_versions(
    session: "Any",
    asset_id: "Any",
    from_version_id: "Any",
    to_version_id: "Any",
) -> DiffResult:
    """Load two versions of an asset from the DB and diff them.

    Both versions must belong to ``asset_id``; raises ValueError if not.

    Read path (strangler):
        * If the asset has a back-filled ``ContentVersion`` chain (its
          ``LineageAsset``, found by the legacy ``Asset.key``, has content
          versions), the two requested AssetVersions are resolved to their
          immutable ``ContentVersion`` bodies and diffed — dispatched by kind
          exactly as the legacy path.
        * Otherwise it FALLS BACK to the legacy ``AssetVersion.body_ref`` path.

    Either way the diff result contract (kind + added/removed/changed) is
    identical, which the golden-equivalence test pins.

    Args:
        session:        Active AsyncSession.
        asset_id:       UUID of the Asset.
        from_version_id: UUID of the "before" AssetVersion.
        to_version_id:  UUID of the "after" AssetVersion.

    Returns:
        DiffResult from the pure diff() function.

    Raises:
        ValueError: if either version doesn't belong to asset_id.
    """
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.models.structure import Asset, AssetVersion

    # Load the parent asset (need its kind for dispatch + its key for lineage).
    asset_result = await session.execute(select(Asset).where(Asset.id == asset_id))
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        raise ValueError(f"Asset {asset_id} not found")

    # Load both versions, scoped to the asset to prevent cross-asset leakage.
    av_result = await session.execute(
        select(AssetVersion).where(
            AssetVersion.id.in_([from_version_id, to_version_id]),
            AssetVersion.asset_id == asset_id,
        )
    )
    versions = {av.id: av for av in av_result.scalars().all()}

    from_av = versions.get(from_version_id)
    to_av = versions.get(to_version_id)

    if from_av is None or to_av is None:
        missing = []
        if from_av is None:
            missing.append(str(from_version_id))
        if to_av is None:
            missing.append(str(to_version_id))
        raise ValueError(f"AssetVersion(s) not found: {', '.join(missing)}")

    if from_av.asset_id != asset_id or to_av.asset_id != asset_id:
        raise ValueError(
            f"AssetVersion(s) do not belong to asset {asset_id}"
        )

    asset_ns = SimpleNamespace(kind=asset.kind)

    def _wrap(body: "str | None") -> SimpleNamespace:
        """Wrap a body string in the DB-agnostic interface the pure diff() uses."""
        return SimpleNamespace(asset_id=asset_id, body_ref=body, asset=asset_ns)

    # --- Immutable content-model read path (preferred) ---
    content_bodies = await _resolve_content_bodies(
        session, asset, from_av, to_av
    )
    if content_bodies is not None:
        from_body, to_body = content_bodies
        return diff(_wrap(from_body), _wrap(to_body))

    # --- Fallback: legacy AssetVersion.body_ref path ---
    return diff(_wrap(from_av.body_ref), _wrap(to_av.body_ref))


async def _resolve_content_bodies(
    session: "Any",
    asset: "Any",
    from_av: "Any",
    to_av: "Any",
) -> "tuple[str, str] | None":
    """Resolve (from, to) AssetVersions to their immutable ``ContentVersion`` bodies.

    Returns ``(from_content, to_content)`` when the asset has a back-filled
    content-version chain AND both requested AssetVersions map onto it; returns
    ``None`` to signal the caller to fall back to the legacy ``body_ref`` path
    (no chain, or a version that wasn't back-filled).

    The map from an AssetVersion to a ContentVersion is by ``content_hash`` —
    recomputed from ``(kind, body_ref, metadata)`` exactly as the back-fill did,
    so dedup and seq-reassignment don't matter (content is the address).
    """
    from sqlalchemy import select

    from app.core.content_hash import content_hash
    from app.models.content_model import ContentVersion, LineageAsset

    # The lineage for this legacy asset (back-fill keyed it on Asset.key).
    lineage = await session.scalar(
        select(LineageAsset).where(LineageAsset.lineage_key == asset.key)
    )
    if lineage is None:
        return None

    kind = asset.kind.value

    async def _content_for(av: "Any") -> "str | None":
        ch = content_hash(kind, av.body_ref, av.metadata_)
        return await session.scalar(
            select(ContentVersion.content).where(
                ContentVersion.asset_id == lineage.id,
                ContentVersion.content_hash == ch,
            )
        )

    from_content = await _content_for(from_av)
    to_content = await _content_for(to_av)

    # If either endpoint has no matching content version, the chain doesn't
    # cover this diff — fall back rather than silently mixing models.
    if from_content is None or to_content is None:
        return None

    return from_content, to_content
