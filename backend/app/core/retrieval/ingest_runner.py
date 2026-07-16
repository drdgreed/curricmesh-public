"""Background runner for release→ingest convergence (retrieval index build).

When a ``CurriculumVersion`` becomes active (released), its retrieval index must
be (re)built so the RAG tutor has something to retrieve. Embedding a whole course
is slow (one governed call per batch of chunks), so a release must NOT block on
it — the release endpoints schedule :func:`run_ingest` on a FastAPI
``BackgroundTask`` AFTER the release commits. This mirrors the async
full-course-generation runner (``app.builder.generation_runner``).

Because the task runs OUTSIDE the request, it CANNOT reuse the request session
(closed once the response is sent) and it must establish tenant scope on its own
session — the request's ``tenant_context`` dependency does not reach here. It
does so through the injected ``session_scope`` factory, which in production is
``app.database.org_scoped_session``: that sets BOTH the ``current_org``
ContextVar (app-layer auto-filter) AND the Postgres ``app.current_org`` GUC on
every transaction begin (DB-layer FORCE-RLS), so every read/write — including the
``ContentChunk`` rows written here — is pinned to ``org_id``. Cross-tenant
isolation therefore holds exactly as it does inside a request.

Guarantees:
* **Idempotent per version.** ``ingest_version`` first deletes the version's
  existing chunks, then rebuilds — so re-releasing / re-scheduling converges to
  the same set rather than duplicating.
* **Never raises.** A background task has no caller to surface an exception to,
  and the release it follows has ALREADY committed. Any ingest failure is logged
  and swallowed so it cannot poison an already-successful release; the retrieval
  index can be rebuilt on demand via the admin trigger.
* **CI-safe embedder.** With no embedder injected, ``get_embedder()`` is used —
  the ``FakeEmbedder`` unless ``EMBEDDING_PROVIDER=hosted``, so NO real embedding
  API is ever hit in tests.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retrieval.embedder import Embedder, get_embedder
from app.core.retrieval.ingest import ingest_version
from app.database import org_scoped_session

logger = logging.getLogger(__name__)

# A factory called as ``async with session_scope(org_id) as session:`` yielding
# an org-scoped ``AsyncSession``. Production default: ``org_scoped_session``.
SessionScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


async def run_ingest(
    version_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    embedder: Embedder | None = None,
    session_scope: SessionScope = org_scoped_session,
) -> None:
    """(Re)build the retrieval index for a just-released curriculum version.

    Opens an org-scoped session, ingests ``version_id`` (idempotent), and commits.
    Never raises: the release has already committed, so an ingest failure is
    recorded to the log and swallowed rather than propagated.
    """
    if embedder is None:
        embedder = get_embedder()
    try:
        async with session_scope(org_id) as session:
            written = await ingest_version(session, version_id, embedder)
            await session.commit()
            logger.info(
                "release-ingest wrote %d chunks for version %s (org %s)",
                written,
                version_id,
                org_id,
            )
    except Exception:  # noqa: BLE001 — background task: log, never propagate
        logger.warning(
            "release-ingest failed for version %s (org %s)",
            version_id,
            org_id,
            exc_info=True,
        )
