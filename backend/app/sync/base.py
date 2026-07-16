"""The external-sync seam (V3-C).

Mirrors the live-SOTA pattern (``app/ai/corpus.py`` / ``app/ai/client.py``): a
``SyncProvider`` Protocol the router depends on, plus pure data carriers
(``VersionManifest`` / ``SyncResult``) that are decoupled from the ORM. The real
providers are *simulated* by default — they format a manifest and return a
deterministic fake URL with ZERO network. A real GitHub/LMS integration is a
documented TODO seam behind config flags + credentials, never exercised in CI.

Errors are surfaced, never swallowed: a provider that cannot publish raises, and
the router records a ``failed`` ``SyncLog`` *and* returns a 4xx/5xx — it never
silently 200s a failure.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class VersionManifest(BaseModel):
    """What we publish — pure data, decoupled from the ORM."""

    curriculum_id: uuid.UUID
    curriculum_name: str
    version: str  # e.g. "v2.3.1"
    modules: list[str]
    released_at: str


class SyncResult(BaseModel):
    """A provider's outcome for one publish attempt."""

    target: str  # "github" | "lms"
    status: str  # "success" | "failed" | "skipped"
    url: str | None
    message: str


@runtime_checkable
class SyncProvider(Protocol):
    """Anything that can publish a ``VersionManifest`` to an external target."""

    async def publish(self, manifest: VersionManifest) -> SyncResult: ...
