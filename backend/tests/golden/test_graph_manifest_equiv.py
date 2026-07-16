"""M2 — dependency-graph manifest read path ⇄ committed golden (equivalence).

Proves the ported graph endpoint, when it builds **from the immutable manifest**
(``active_curriculum_version`` resolves -> the manifest branch of
``app/routers/graph.py``), reproduces the legacy-captured golden fixtures for both
seeded curricula. The golden was captured from the legacy structure-table path
(``tests/golden/fixtures/*.json``), so equivalence proves the port.

The captured shape is normalized exactly like ``tests/golden/capture.capture_graph``
(nodes keyed on the stable lineage key, with ``kind`` / friendly ``label`` /
``latest_version`` / ``status`` / ``misaligned``; edges on from/to lineage keys +
``edge_type``; the misaligned key set). The only difference from
``capture_graph`` is the id->key map: the manifest path's node/edge ids are
``LineageAsset`` ids, so we resolve them via ``LineageAsset.id -> lineage_key``
(== ``Asset.key``) instead of ``Asset.id -> Asset.key``.

Per P-006 this test **seeds + back-fills within the test** on its own dedicated
engine (the shared ``db_session`` fixture recreates an empty schema), so it never
depends on a pre-seeded DB.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.config import settings
from app.core.manifest import active_curriculum_version
from app.database import Base
from app.db.rls import apply_rls
from app.migration.backfill_content_model import backfill_content_model
from app.models.content_model import LineageAsset
from app.models.curriculum import Curriculum
from app.models.org import Organization
from app.routers.graph import get_curriculum_graph
from app.tenant import current_org, use_org
from seed.bootcamp_curriculum import seed
from tests.golden.capture import assert_equivalent, capture_graph

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# The two seeded curricula whose goldens we assert the manifest path against.
SEEDED_SLUGS = ("agentic-ai", "cloud-data-eng")

_ENUM_TYPES = ("lifecyclestatus", "assetkind")

# The endpoint body never reads the auth dependency (auth is enforced by the
# Depends wrapper, which we bypass — the harness is already inside tenant context).
_STUB_USER: dict[str, Any] = {}


@pytest.fixture
async def seeded_backfilled_engine():
    """A dedicated engine on a freshly-seeded **and back-filled** schema.

    Rebuilds the schema (drop+create), applies RLS, runs the real seed (both demo
    orgs / curricula), then back-fills the immutable content model so the manifest
    branch of the graph endpoint is exercised.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)

    async with session_factory() as session:
        await seed(session)
        await backfill_content_model(session)

    yield engine
    await engine.dispose()


def _load_fixture(slug: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{slug}.json").read_text())


async def _lineage_key_map(db: AsyncSession) -> dict[uuid.UUID, str]:
    """Map every LineageAsset id in the current tenant to its stable lineage key."""
    rows = await db.execute(select(LineageAsset.id, LineageAsset.lineage_key))
    return {r[0]: r[1] for r in rows.all()}


def _key(id_map: dict[uuid.UUID, str], asset_id: uuid.UUID) -> str:
    return id_map.get(asset_id, f"<unknown:{asset_id}>")


async def _capture_manifest_graph(
    db: AsyncSession, curriculum_id: uuid.UUID
) -> dict[str, Any]:
    """Capture the (manifest-path) graph endpoint output, normalized on lineage keys.

    Identical normalized shape to ``tests/golden/capture.capture_graph`` — the
    only difference is the id->key map (LineageAsset ids, not Asset ids), since the
    manifest path emits LineageAsset ids as node/edge ids.
    """
    id_map = await _lineage_key_map(db)
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


async def _org_ids(engine) -> list[uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return [r[0] for r in (await s.execute(select(Organization.id))).all()]


async def _open_org_session(engine, org_id: uuid.UUID):
    """Open a session pinned to ``org_id`` (ContextVar set by caller + GUC here)."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


async def test_manifest_graph_equals_golden(seeded_backfilled_engine, capsys):
    """The manifest-path graph output ``assert_equivalent`` the committed golden.

    For each org, for each seeded curriculum: assert a manifest is resolved (so the
    endpoint takes the manifest branch), capture the normalized graph, and assert
    it is equivalent to the committed legacy-captured golden fixture.
    """
    engine = seeded_backfilled_engine
    org_ids = await _org_ids(engine)
    assert org_ids, "seed created no organizations"

    checked: set[str] = set()
    report_lines: list[str] = []

    for org_id in org_ids:
        token = current_org.set(org_id)
        try:
            with use_org(org_id):
                session = await _open_org_session(engine, org_id)
                try:
                    curricula = (
                        (await session.execute(select(Curriculum))).scalars().all()
                    )
                    for curriculum in curricula:
                        if curriculum.slug not in SEEDED_SLUGS:
                            continue

                        # Manifest branch must be taken (a back-filled version).
                        cversion = await active_curriculum_version(
                            session, curriculum.id
                        )
                        assert cversion is not None, (
                            f"{curriculum.slug}: no active CurriculumVersion — "
                            "the graph endpoint would fall back to legacy"
                        )

                        # The ported endpoint emits LEGACY Asset ids (the API
                        # contract), so the standard capture_graph (Asset.id->key)
                        # normalizes it exactly like the legacy golden capture.
                        actual = await capture_graph(session, curriculum.id)
                        expected = _load_fixture(curriculum.slug)["graph"]

                        assert_equivalent(expected, actual)

                        checked.add(curriculum.slug)
                        report_lines.append(
                            f"[{curriculum.slug}] manifest graph == golden: "
                            f"nodes={len(actual['nodes'])} "
                            f"edges={len(actual['edges'])} "
                            f"misaligned={len(actual['misaligned'])}"
                        )
                finally:
                    await session.rollback()
                    await session.close()
        finally:
            current_org.reset(token)

    assert checked == set(SEEDED_SLUGS), (
        f"expected to check both seeded curricula {SEEDED_SLUGS}, checked {sorted(checked)}"
    )

    with capsys.disabled():
        print("\n=== manifest-path graph vs committed golden ===")
        for line in report_lines:
            print(line)
