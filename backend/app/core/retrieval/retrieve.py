"""Retrieval — top-k cosine search over a version's chunks (Phase B, Task 4).

``retrieve`` embeds the query, then returns the ``k`` nearest ``ContentChunk``
rows by **cosine distance** (pgvector's ``<=>`` operator, via the model's
``embedding.cosine_distance``), scoped to a single curriculum version.

Isolation — two independent guards, both active:
* **Version scoping** is explicit: ``WHERE curriculum_version_id = version_id``.
  A version's chunks are never mixed with another version's.
* **Tenant scoping** is automatic: ``ContentChunk`` is ``TenantScoped``, so the
  app-layer ``do_orm_execute`` filter adds ``organization_id == current_org`` to
  this SELECT (and Postgres RLS backs it at the DB layer). A caller in org B who
  passes org A's ``version_id`` gets nothing — not a cross-tenant leak.

Because it rides the ORM ``select``, both guards apply without any manual
``organization_id`` predicate here — the tenant scope cannot be forgotten.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import Embedder
from app.models.retrieval import ContentChunk


async def retrieve(
    session: AsyncSession,
    version_id: uuid.UUID,
    query: str,
    k: int,
    embedder: Embedder,
) -> list[ContentChunk]:
    """Return the ``k`` chunks of ``version_id`` nearest to ``query`` (cosine).

    Tenant-scoped automatically (TenantScoped + RLS) and version-scoped
    explicitly. Returns fewer than ``k`` rows when the version's index is
    smaller (or empty).
    """
    if k <= 0:
        return []
    [query_vec] = await embedder.embed([query])
    stmt = (
        select(ContentChunk)
        .where(ContentChunk.curriculum_version_id == version_id)
        .order_by(ContentChunk.embedding.cosine_distance(query_vec))
        .limit(k)
    )
    return list((await session.execute(stmt)).scalars().all())
