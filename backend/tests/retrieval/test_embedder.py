"""Embedder seam tests (Phase B retrieval infra, Task 2).

Covers the provider-abstracted embedder:
  - ``FakeEmbedder`` is deterministic (same text -> same vector), unit-norm, and
    emits the configured dimension. Distinct text -> distinct vectors.
  - ``get_embedder`` returns the Fake for EMBEDDING_PROVIDER=fake (the CI/dev
    default) and the hosted default otherwise.
  - ``HostedEmbedder`` records governed telemetry per embed call (spend/latency
    visibility, like every other AI call) WITHOUT any real network — the
    provider call is injected.

No real embedding API is ever called here.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.retrieval.embedder import (
    Embedder,
    FakeEmbedder,
    HostedEmbedder,
    get_embedder,
)


@pytest.mark.asyncio
async def test_fake_embedder_deterministic_and_dim():
    emb = FakeEmbedder()
    a1 = await emb.embed(["agentic ai patterns"])
    a2 = await emb.embed(["agentic ai patterns"])
    assert a1 == a2, "same text must embed identically (deterministic)"
    assert len(a1) == 1
    assert len(a1[0]) == settings.EMBEDDING_DIM == emb.dim


@pytest.mark.asyncio
async def test_fake_embedder_distinct_texts_differ():
    emb = FakeEmbedder()
    [va], [vb] = await emb.embed(["alpha"]), await emb.embed(["beta"])
    assert va != vb


@pytest.mark.asyncio
async def test_fake_embedder_unit_norm():
    emb = FakeEmbedder()
    [v] = await emb.embed(["something"])
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_fake_embedder_custom_dim():
    emb = FakeEmbedder(dim=8)
    [v] = await emb.embed(["x"])
    assert emb.dim == 8
    assert len(v) == 8


def test_get_embedder_default_is_fake():
    assert isinstance(get_embedder(), FakeEmbedder)


def test_get_embedder_hosted_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "hosted")
    emb = get_embedder()
    assert isinstance(emb, HostedEmbedder)
    assert isinstance(emb, Embedder)  # satisfies the Protocol


@pytest.mark.asyncio
async def test_hosted_embedder_records_governed_telemetry():
    """HostedEmbedder emits one telemetry record per embed call — no network."""
    recorded: list[dict] = []

    async def fake_provider_embed(texts: list[str]) -> list[list[float]]:
        # Stand-in for the real provider HTTP call (never hit in CI).
        return [[0.0] * settings.EMBEDDING_DIM for _ in texts]

    def spy_record(**kwargs):
        recorded.append(kwargs)

    emb = HostedEmbedder(
        model="voyage-3",
        dim=settings.EMBEDDING_DIM,
        provider_embed=fake_provider_embed,
        record_event=spy_record,
    )
    out = await emb.embed(["one", "two"])
    assert len(out) == 2
    assert len(recorded) == 1
    assert recorded[0]["model"] == "voyage-3"
    assert recorded[0]["feature"] == "embedding"
