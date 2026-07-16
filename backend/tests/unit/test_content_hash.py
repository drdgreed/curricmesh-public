"""Unit tests for app/core/content_hash.py — pure function, no DB.

The hash is the *identity* of an immutable content version, so determinism and
sensitivity-to-meaningful-change are the contract:
  - identical (kind, content, metadata) → identical hash (enables sharing/dedup);
  - metadata dict ordering must not matter (sorted-keys serialization);
  - changing any component changes the hash;
  - None content/metadata are encoded distinctly from "" / {}.
"""

from __future__ import annotations

from app.core.content_hash import content_hash


def test_deterministic_same_input_same_hash():
    h1 = content_hash("lesson_plan", "body", {"a": 1, "b": 2})
    h2 = content_hash("lesson_plan", "body", {"a": 1, "b": 2})
    assert h1 == h2


def test_sha256_hex_shape():
    h = content_hash("slides", "x", None)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_metadata_key_order_irrelevant():
    # Different insertion order, same logical content → same hash.
    a = content_hash("rubric", "c", {"a": 1, "b": 2, "z": 3})
    b = content_hash("rubric", "c", {"z": 3, "b": 2, "a": 1})
    assert a == b


def test_changing_kind_changes_hash():
    assert content_hash("lesson_plan", "x", None) != content_hash("slides", "x", None)


def test_changing_content_changes_hash():
    assert content_hash("spec", "v1", None) != content_hash("spec", "v2", None)


def test_changing_metadata_changes_hash():
    assert content_hash("spec", "x", {"a": 1}) != content_hash("spec", "x", {"a": 2})


def test_none_distinct_from_empty():
    # None content/metadata must not collide with empty string / empty dict.
    assert content_hash("spec", None, None) != content_hash("spec", "", None)
    assert content_hash("spec", "x", None) != content_hash("spec", "x", {})
