"""Token-bounded text chunker for retrieval ingestion (Phase B, Task 3).

Splits an item's body into overlapping, token-bounded chunks suitable for
embedding. Deliberately simple (KISS, no tokenizer dependency):

* **Token estimate.** We approximate tokens by **whitespace-delimited words**.
  This over-counts slightly vs a BPE tokenizer but is stable, dependency-free,
  and conservative (chunks stay under a real model's token limit). ``max_tokens``
  and ``overlap`` are therefore in *word* units.
* **Windowing.** A sliding window of ``max_tokens`` words advances by
  ``max_tokens - overlap`` words, so adjacent chunks share ``overlap`` words of
  context (which helps a chunk that straddles a concept boundary stay
  retrievable). ``overlap`` is clamped to ``< max_tokens`` to guarantee forward
  progress.
* **Empty/whitespace input** yields no chunks (nothing to index).

Returned chunks preserve document order; each carries its own word count.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Chunk:
    """One token-bounded slice of source text + its (approx) token count."""

    text: str
    token_count: int


def chunk_text(
    text: str,
    *,
    max_tokens: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """Split ``text`` into overlapping, word-bounded chunks (see module docstring)."""
    max_tokens = max_tokens if max_tokens is not None else settings.RETRIEVAL_CHUNK_TOKENS
    overlap = overlap if overlap is not None else settings.RETRIEVAL_CHUNK_OVERLAP
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    # Guarantee forward progress: the stride (max_tokens - overlap) must be >= 1.
    overlap = max(0, min(overlap, max_tokens - 1))

    words = text.split()
    if not words:
        return []

    stride = max_tokens - overlap
    chunks: list[Chunk] = []
    for start in range(0, len(words), stride):
        window = words[start : start + max_tokens]
        if not window:
            break
        chunks.append(Chunk(text=" ".join(window), token_count=len(window)))
        # The last window reached the end — stop (avoid a trailing overlap-only
        # duplicate chunk).
        if start + max_tokens >= len(words):
            break
    return chunks
