"""Capture + normalize + compare the CURRENT read-path outputs (Task G).

This module is deliberately *independent of the new immutable model*. It drives
the existing endpoint functions (``get_curriculum_graph``, ``get_dashboard``,
``get_asset_diff``) against the seeded data and folds their responses into a
**normalized** JSON shape that is stable across re-seeds.

Normalization rules (so a re-seed yields byte-identical goldens):
  * Volatile UUIDs are replaced by **stable lineage keys**: an asset's ``key``
    column (e.g. ``"agentic-ai/v1/03/lab"``). Curricula are keyed by ``slug``.
  * Wall-clock timestamps are NEVER emitted. Where a timestamp carries semantic
    meaning (alignment staleness), it is reduced to a deterministic *relation*
    (``dependent_is_stale`` = dependent_updated_at < dependency_updated_at).
  * Every list is sorted by its stable key so ordering is deterministic.

The captured dicts are what we commit under ``fixtures/`` and what the M2
ports assert equivalence against via :func:`assert_equivalent`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.structure import Asset, AssetVersion
from app.routers.diff import get_asset_diff
from app.routers.graph import get_curriculum_graph
from app.routers.dashboard import get_dashboard

# A stand-in for the ``current`` user dependency. The endpoint *bodies* never
# read it (auth is enforced by the Depends wrapper, which we bypass here because
# the harness already operates inside the tenant context), so an empty dict is
# sufficient and keeps the capture faithful to the real endpoint code path.
_STUB_USER: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# id -> stable-key resolution
# ---------------------------------------------------------------------------


async def _asset_key_map(db: AsyncSession) -> dict[uuid.UUID, str]:
    """Map every Asset id in the current tenant to its stable ``key`` lineage string."""
    result = await db.execute(select(Asset.id, Asset.key))
    return {row[0]: row[1] for row in result.all()}


def _key(id_map: dict[uuid.UUID, str], asset_id: uuid.UUID) -> str:
    """Resolve an asset id to its stable key, or a clearly-marked fallback."""
    return id_map.get(asset_id, f"<unknown:{asset_id}>")


# ---------------------------------------------------------------------------
# Graph capture
# ---------------------------------------------------------------------------


async def capture_graph(db: AsyncSession, curriculum_id: uuid.UUID) -> dict[str, Any]:
    """Capture the dependency-graph endpoint output, normalized on asset keys.

    Returns a dict::

        {
          "nodes": [ {key, kind, label, latest_version, status, misaligned}, ... ],
          "edges": [ {from_key, to_key, edge_type}, ... ],
          "misaligned": [ key, ... ],
        }

    Nodes are sorted by ``key``; edges by ``(from_key, to_key, edge_type)``;
    ``misaligned`` by key. No UUIDs, no timestamps.
    """
    id_map = await _asset_key_map(db)
    graph = await get_curriculum_graph(curriculum_id, current=_STUB_USER, db=db)

    misaligned_keys = sorted(_key(id_map, aid) for aid in graph.misaligned_asset_ids)
    misaligned_set = set(misaligned_keys)

    nodes = sorted(
        (
            {
                "key": _key(id_map, n.id),
                "kind": n.kind.value if hasattr(n.kind, "value") else str(n.kind),
                "label": n.label,
                "latest_version": n.latest_version,
                "status": (
                    n.status.value if hasattr(n.status, "value") else n.status
                ),
                "misaligned": _key(id_map, n.id) in misaligned_set,
            }
            for n in graph.nodes
        ),
        key=lambda d: d["key"],
    )

    edges = sorted(
        (
            {
                "from_key": _key(id_map, e.from_asset_id),
                "to_key": _key(id_map, e.to_asset_id),
                "edge_type": e.edge_type,
            }
            for e in graph.edges
        ),
        key=lambda d: (d["from_key"], d["to_key"], d["edge_type"]),
    )

    return {"nodes": nodes, "edges": edges, "misaligned": misaligned_keys}


# ---------------------------------------------------------------------------
# Dashboard-alignment capture
# ---------------------------------------------------------------------------


async def capture_dashboard_alignment(db: AsyncSession) -> dict[str, Any]:
    """Capture the dashboard's per-curriculum alignment entries, normalized.

    Returns a dict keyed by curriculum ``slug``::

        {
          "<slug>": {
            "name": str,
            "alignment": [
              {dependent_key, dependency_key, dependent_name,
               dependency_name, dependent_is_stale}, ...
            ],
          },
          ...
        }

    The ``reason`` string and raw ``*_updated_at`` timestamps are intentionally
    dropped (they embed UUIDs / wall-clock times). Their *semantic* content —
    that the dependent's latest update predates its dependency's — is preserved
    as the boolean ``dependent_is_stale``.
    """
    id_map = await _asset_key_map(db)
    dash = await get_dashboard(current=_STUB_USER, db=db)

    out: dict[str, Any] = {}
    for entry in dash.curricula:
        alignment = sorted(
            (
                {
                    "dependent_key": _key(id_map, m.dependent_asset_id),
                    "dependency_key": _key(id_map, m.dependency_asset_id),
                    "dependent_name": m.dependent_asset_name,
                    "dependency_name": m.dependency_asset_name,
                    "dependent_is_stale": _is_stale(
                        m.dependent_updated_at, m.dependency_updated_at
                    ),
                }
                for m in entry.alignment
            ),
            key=lambda d: (d["dependent_key"], d["dependency_key"]),
        )
        out[entry.slug] = {"name": entry.name, "alignment": alignment}

    # Deterministic top-level ordering (dict preserves insertion order in JSON).
    return {slug: out[slug] for slug in sorted(out)}


def _is_stale(dependent_at: Any, dependency_at: Any) -> bool | None:
    """Reduce two timestamps to the staleness relation, dropping the raw values."""
    if dependent_at is None or dependency_at is None:
        return None
    return dependent_at < dependency_at


# ---------------------------------------------------------------------------
# Asset-diff capture
# ---------------------------------------------------------------------------


async def find_diffable_asset(
    db: AsyncSession,
    kinds: frozenset[str] | None = None,
) -> tuple[uuid.UUID, str, uuid.UUID, str, uuid.UUID, str]:
    """Find an asset with >=2 AssetVersions and return a stable handle on it.

    Returns ``(asset_id, asset_key, from_version_id, from_semver,
    to_version_id, to_semver)`` for the asset with the most versions (ties broken
    by ``key``), diffing its lowest semver against its highest. Deterministic.

    ``kinds`` optionally restricts the candidate set to assets of those kinds
    (used to deterministically pick a *structured* kind so the structured
    added/removed/changed diff shape gets exercised).
    """
    av_rows = await db.execute(
        select(
            AssetVersion.id,
            AssetVersion.asset_id,
            AssetVersion.major,
            AssetVersion.minor,
            AssetVersion.patch,
        )
    )
    by_asset: dict[uuid.UUID, list[tuple[uuid.UUID, tuple[int, int, int]]]] = {}
    for vid, aid, ma, mi, pa in av_rows.all():
        by_asset.setdefault(aid, []).append((vid, (ma, mi, pa)))

    key_map = await _asset_key_map(db)
    kind_map: dict[uuid.UUID, str] = {}
    if kinds is not None:
        kind_rows = await db.execute(select(Asset.id, Asset.kind))
        kind_map = {
            aid: (k.value if hasattr(k, "value") else str(k))
            for aid, k in kind_rows.all()
        }

    candidates = [
        (aid, versions)
        for aid, versions in by_asset.items()
        if len(versions) >= 2 and (kinds is None or kind_map.get(aid) in kinds)
    ]
    if not candidates:
        raise LookupError(
            f"no asset has >=2 AssetVersions in this tenant (kinds={kinds})"
        )

    # Most versions wins; tie-break on the stable key for determinism.
    aid, versions = max(
        candidates,
        key=lambda c: (len(c[1]), key_map.get(c[0], "")),
    )
    versions_sorted = sorted(versions, key=lambda v: v[1])
    from_id, from_sv = versions_sorted[0]
    to_id, to_sv = versions_sorted[-1]
    semver = lambda t: f"{t[0]}.{t[1]}.{t[2]}"  # noqa: E731
    return (
        aid,
        key_map[aid],
        from_id,
        semver(from_sv),
        to_id,
        semver(to_sv),
    )


# The diff service's structured kinds (rubric, learning_objectives) exercise the
# added/removed/changed shape; everything else is a line-based text diff.
_STRUCTURED_KINDS = frozenset({"rubric", "learning_objectives"})


async def _capture_one_diff(
    db: AsyncSession, kinds: frozenset[str] | None
) -> dict[str, Any]:
    """Capture a single asset diff for the deterministic pick within ``kinds``."""
    (
        asset_id,
        asset_key,
        from_id,
        from_sv,
        to_id,
        to_sv,
    ) = await find_diffable_asset(db, kinds=kinds)

    diff = await get_asset_diff(
        asset_id, from_=from_id, to=to_id, current=_STUB_USER, db=db
    )

    text = (
        {"added": diff.text.added, "removed": diff.text.removed, "unified": diff.text.unified}
        if diff.text is not None
        else None
    )
    structured = (
        {
            "added": diff.structured.added,
            "removed": diff.structured.removed,
            "changed": diff.structured.changed,
        }
        if diff.structured is not None
        else None
    )
    return {
        "asset_key": asset_key,
        "from_semver": from_sv,
        "to_semver": to_sv,
        "kind": diff.kind,
        "text": text,
        "structured": structured,
    }


async def capture_asset_diff(db: AsyncSession) -> dict[str, Any]:
    """Capture two representative asset diffs, keyed on stable asset key + semvers.

    Returns::

        {
          "structured": {asset_key, from_semver, to_semver, kind,
                         text=None, structured={added, removed, changed}},
          "representative": {asset_key, from_semver, to_semver, kind, text, structured},
        }

    * ``structured`` deterministically picks a rubric / learning-objectives asset
      with >=2 versions, exercising the structured added/removed/changed shape.
    * ``representative`` picks the asset with the most versions overall (a text
      kind in the seed), exercising the text added/removed/unified shape.

    If no structured-kind asset has >=2 versions, the ``structured`` slot is
    omitted (so the harness still works on a leaner seed).
    """
    out: dict[str, Any] = {
        "representative": await _capture_one_diff(db, kinds=None),
    }
    try:
        out["structured"] = await _capture_one_diff(db, kinds=_STRUCTURED_KINDS)
    except LookupError:
        pass
    return out


# ---------------------------------------------------------------------------
# Full per-curriculum capture
# ---------------------------------------------------------------------------


async def curriculum_id_for_slug(db: AsyncSession, slug: str) -> uuid.UUID:
    """Resolve a curriculum slug to its id within the current tenant context."""
    from app.models.curriculum import Curriculum

    result = await db.execute(select(Curriculum.id).where(Curriculum.slug == slug))
    cid = result.scalar_one_or_none()
    if cid is None:
        raise LookupError(f"no curriculum with slug {slug!r} in this tenant")
    return cid


async def capture_all(db: AsyncSession, slug: str) -> dict[str, Any]:
    """Capture graph + dashboard-alignment + diff for one seeded curriculum.

    ``slug`` selects the curriculum (the graph + diff are tenant-scoped to it;
    the dashboard alignment is captured for the whole tenant, which at seed time
    is exactly this one curriculum).
    """
    curriculum_id = await curriculum_id_for_slug(db, slug)
    return {
        "slug": slug,
        "graph": await capture_graph(db, curriculum_id),
        "dashboard_alignment": await capture_dashboard_alignment(db),
        "asset_diff": await capture_asset_diff(db),
    }


# ---------------------------------------------------------------------------
# Equivalence comparator (the safety net the M2 ports call)
# ---------------------------------------------------------------------------


class GoldenMismatch(AssertionError):
    """Raised by :func:`assert_equivalent` when normalized outputs differ."""


def _normalize_for_compare(value: Any) -> Any:
    """Recursively canonicalize a captured structure for order-insensitive compare.

    Lists of dicts are sorted by a stable key derived from the dict's own
    identifying fields (``key`` / ``from_key`` / ``dependent_key`` / ...), so
    two captures that differ only in list ordering compare equal. Plain scalar
    lists (e.g. ``misaligned``, diff ``added``) are sorted by their string form.

    This makes the comparator order-insensitive *where order is not semantic*.
    Diff ``unified`` text and ``added``/``removed`` line lists are content and
    are normalized by sorting too, since the endpoint's line order is not part
    of the equivalence contract for this baseline (the *set* of changes is).
    """
    if isinstance(value, dict):
        return {k: _normalize_for_compare(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        items = [_normalize_for_compare(v) for v in value]
        return sorted(items, key=_sort_key)
    return value


# The identifying fields we sort dict-lists on, in priority order.
_ID_FIELDS = (
    "key",
    "asset_key",
    "slug",
    "dependent_key",
    "from_key",
)


def _sort_key(item: Any) -> str:
    """Produce a deterministic, type-stable sort key for any normalized item."""
    if isinstance(item, dict):
        for field in _ID_FIELDS:
            if field in item:
                # Compose with a second field where a single id is ambiguous.
                second = item.get("to_key") or item.get("dependency_key") or ""
                return f"{item[field]}|{second}"
        # No identifying field: fall back to a canonical repr.
        return repr(sorted(item.items()))
    return f"{type(item).__name__}:{item!r}"


def assert_equivalent(expected_golden: Any, actual: Any) -> None:
    """Assert that ``actual`` is read-path-equivalent to ``expected_golden``.

    Equivalence is:
      * **order-insensitive** for lists where ordering is not semantic (both
        sides are canonically sorted before comparison), and
      * **stable-key based** — both sides are already keyed on lineage keys /
        slugs / semvers, never on volatile UUIDs or timestamps.

    Raises :class:`GoldenMismatch` with a focused first-difference message on
    failure. Intended to be called by the M2 golden-equivalence tests as
    ``assert_equivalent(loaded_golden, freshly_captured_new_path_output)``.
    """
    exp = _normalize_for_compare(expected_golden)
    act = _normalize_for_compare(actual)
    if exp != act:
        diff_path, exp_v, act_v = _first_difference(exp, act, "")
        raise GoldenMismatch(
            f"golden mismatch at {diff_path or '<root>'}:\n"
            f"  expected: {exp_v!r}\n"
            f"  actual:   {act_v!r}"
        )


def _first_difference(exp: Any, act: Any, path: str) -> tuple[str, Any, Any]:
    """Walk two normalized structures and return the path of the first divergence."""
    if isinstance(exp, dict) and isinstance(act, dict):
        for k in sorted(set(exp) | set(act)):
            if k not in exp or k not in act:
                return f"{path}.{k}", exp.get(k, "<absent>"), act.get(k, "<absent>")
            if exp[k] != act[k]:
                return _first_difference(exp[k], act[k], f"{path}.{k}")
    elif isinstance(exp, list) and isinstance(act, list):
        if len(exp) != len(act):
            return f"{path}[len]", len(exp), len(act)
        for i, (e, a) in enumerate(zip(exp, act)):
            if e != a:
                return _first_difference(e, a, f"{path}[{i}]")
    return path, exp, act
