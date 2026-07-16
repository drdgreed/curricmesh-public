"""Embedder seam — provider-abstracted text embeddings (Phase B, Task 2).

Mirrors the storage-adapter pattern (``app/media/storage.py``): a
``@runtime_checkable`` Protocol, a deterministic in-memory Fake for tests/CI,
and a real hosted default. ``get_embedder()`` chooses between them from config.

Design (per spec D3 — "provider abstraction with a hosted default"):
* ``Embedder`` — the seam the rest of the retrieval code depends on. Anything
  with ``dim`` and ``async embed(texts) -> vectors`` satisfies it.
* ``FakeEmbedder`` — deterministic (SHA-256 of the text → a unit vector of the
  configured dimension). Same text → identical vector, so an exact-match query
  retrieves its chunk at cosine distance 0. This is a WIRING fake, not a
  semantic model: it makes retrieval *testable offline*, it does not model
  meaning. It is the default so NO real embedding API is ever hit in CI.
* ``HostedEmbedder`` — the real governed default. Every batch is wrapped in the
  same per-call telemetry the rest of the app uses (``usage.record`` +
  ``usage_store.record_event``), so embedding spend/latency is governed like any
  other AI call. The actual provider HTTP call is injected (``provider_embed``)
  and built lazily — importing this module never requires a key or a network.

Config: ``EMBEDDING_PROVIDER`` (fake|hosted), ``EMBEDDING_MODEL``,
``EMBEDDING_DIM``, ``EMBEDDING_API_KEY``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Awaitable, Callable, Protocol, runtime_checkable

from app.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """The seam: turns texts into fixed-width embedding vectors."""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one unit-or-provider vector per input text, in order."""
        ...


# ---------------------------------------------------------------------------
# FakeEmbedder (tests / dev default — deterministic, offline)
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic in-memory embedder — identical text ⇒ identical unit vector.

    Expands SHA-256(text) into ``dim`` floats in [-1, 1], then L2-normalizes so
    cosine distance is well-defined. Deterministic and dependency-free: the same
    text always yields the same vector (so an exact-match query lands at distance
    0), and distinct texts yield distinct vectors with overwhelming probability.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim if dim is not None else settings.EMBEDDING_DIM

    def _vector(self, text: str) -> list[float]:
        # Stretch the digest deterministically to ``dim`` bytes by hashing
        # (text || counter) until we have enough material.
        raw = bytearray()
        counter = 0
        while len(raw) < self.dim:
            raw.extend(
                hashlib.sha256(f"{text}\x00{counter}".encode()).digest()
            )
            counter += 1
        # Map each byte 0..255 → [-1, 1].
        vals = [(b / 127.5) - 1.0 for b in raw[: self.dim]]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


# ---------------------------------------------------------------------------
# HostedEmbedder (real provider — governed via telemetry)
# ---------------------------------------------------------------------------

# A provider call: batch of texts -> batch of vectors. Injected so the class is
# testable offline and the concrete provider (Voyage-class, etc.) is swappable.
ProviderEmbed = Callable[[list[str]], Awaitable[list[list[float]]]]


class HostedEmbedder:
    """Real embedder backed by a hosted provider, governed by usage telemetry.

    The provider HTTP call (``provider_embed``) is injected. In production
    ``get_embedder`` supplies one built lazily from ``EMBEDDING_API_KEY`` /
    ``EMBEDDING_MODEL``; tests inject a stub. Every ``embed`` call records one
    telemetry event (model + latency + an input-token estimate) so embedding
    spend is visible alongside the rest of the AI usage — best-effort: a
    telemetry failure warns but never breaks the embed call.
    """

    def __init__(
        self,
        *,
        model: str,
        dim: int,
        provider_embed: ProviderEmbed | None = None,
        record_event: Callable[..., None] | None = None,
    ) -> None:
        self.model = model
        self.dim = dim
        self._provider_embed = provider_embed
        # Default to the durable usage writer; injectable for tests.
        if record_event is None:
            from app.ai import usage_store

            record_event = usage_store.record_event
        self._record_event = record_event

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._provider_embed is None:
            raise RuntimeError(
                "HostedEmbedder has no provider_embed configured; set "
                "EMBEDDING_API_KEY (and a provider) or use the FakeEmbedder."
            )
        start = time.monotonic()
        vectors = await self._provider_embed(texts)
        latency_ms = int((time.monotonic() - start) * 1000)
        # Coarse token estimate (~4 chars/token) — embeddings bill on input
        # tokens; this is for governance visibility, not exact billing.
        approx_tokens = sum(len(t) for t in texts) // 4
        try:
            self._record_event(
                model=self.model,
                feature="embedding",
                input_tokens=approx_tokens,
                output_tokens=0,
                latency_ms=latency_ms,
                stop_reason=None,
            )
        except Exception:  # noqa: BLE001 — telemetry must never break the call
            logger.warning("embedding usage record_event() failed", exc_info=True)
        return vectors


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_embedder() -> Embedder:
    """Return the configured embedder.

    ``EMBEDDING_PROVIDER=fake`` (the default) → ``FakeEmbedder`` (offline,
    deterministic). ``=hosted`` → ``HostedEmbedder`` wired to the real provider.
    The real provider client is built lazily inside ``_hosted_provider_embed``
    so importing this module never needs a key or a network.
    """
    if settings.EMBEDDING_PROVIDER == "hosted":
        return HostedEmbedder(
            model=settings.EMBEDDING_MODEL,
            dim=settings.EMBEDDING_DIM,
            provider_embed=_hosted_provider_embed,
        )
    return FakeEmbedder()


async def _hosted_provider_embed(texts: list[str]) -> list[list[float]]:
    """Real hosted-provider embedding call — the swappable integration point.

    Intentionally unimplemented in this build: Foundation 1 delivers the seam +
    the deterministic path, and no real embedding provider is called in CI. A
    later convergence step wires a concrete provider (e.g. Voyage) here, reading
    ``settings.EMBEDDING_API_KEY`` / ``settings.EMBEDDING_MODEL``.
    """
    raise NotImplementedError(
        "No hosted embedding provider is wired yet. Foundation 1 ships the "
        "FakeEmbedder path; set EMBEDDING_PROVIDER=fake, or implement "
        "_hosted_provider_embed for a real deployment."
    )
