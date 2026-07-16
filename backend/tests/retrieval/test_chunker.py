"""Token-bounded chunker tests (Phase B retrieval infra, Task 3)."""

from __future__ import annotations

import pytest

from app.core.retrieval.chunker import Chunk, chunk_text


def test_empty_and_whitespace_yield_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\t ") == []


def test_short_text_is_a_single_chunk():
    chunks = chunk_text("alpha beta gamma", max_tokens=100, overlap=10)
    assert chunks == [Chunk(text="alpha beta gamma", token_count=3)]


def test_windows_are_bounded_and_overlap():
    words = [f"w{i}" for i in range(10)]
    chunks = chunk_text(" ".join(words), max_tokens=4, overlap=1)
    # stride = 3 → windows start at 0,3,6; start=6 covers w6..w9 and reaches the
    # end, so no redundant trailing chunk is emitted.
    assert [c.token_count for c in chunks] == [4, 4, 4]
    assert chunks[0].text == "w0 w1 w2 w3"
    # Adjacent chunks share `overlap` (1) trailing/leading word: w3 ends c0, starts c1.
    assert chunks[1].text.startswith("w3")
    # Every source word is covered.
    covered = " ".join(c.text for c in chunks).split()
    assert set(covered) == set(words)


def test_overlap_clamped_below_max_guarantees_progress():
    # overlap >= max_tokens would stall; it must be clamped so we still advance.
    chunks = chunk_text("a b c d e", max_tokens=2, overlap=5)
    # Reconstruct-ability: every source word appears in some chunk.
    seen = " ".join(c.text for c in chunks).split()
    assert set(seen) == {"a", "b", "c", "d", "e"}
    assert len(chunks) >= 1


def test_max_tokens_must_be_positive():
    with pytest.raises(ValueError):
        chunk_text("a b c", max_tokens=0)
