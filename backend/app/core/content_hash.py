"""Content-addressing for the immutable version model.

A :class:`~app.models.content_model.ContentVersion` is an immutable blob: its
identity is its *content*, not a surrogate UUID. ``content_hash`` derives that
identity — a stable sha256 over the normalized ``(kind, content, metadata)``
triple — so that:

* identical content (same kind/body/metadata) always yields the same hash,
  enabling structural sharing and dedup (write once, reference many);
* an ``fsck``-style integrity pass can verify every stored row by recomputing
  its hash and comparing to the persisted ``content_hash``.

Determinism is the whole point, so the serialization is pinned:
``json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.
Sorted keys make dict ordering irrelevant; the compact separators avoid
incidental whitespace differences. ``None`` content/metadata are encoded as
JSON ``null`` (distinct from the empty string ``""`` or empty object ``{}``).

Import-light by design (only the stdlib) so it can be pulled into migrations,
seeders, and the model layer without dragging in ``app.models``.
"""

from __future__ import annotations

import hashlib
import json


def content_hash(
    kind: str,
    content: str | None,
    metadata: dict | None,
) -> str:
    """Return the sha256 hex digest of the normalized content triple.

    The hash covers ``{"kind": kind, "content": content, "metadata": metadata}``
    serialized as canonical, sorted-keys JSON. Deterministic: the same inputs
    always produce the same 64-char lowercase hex string, regardless of dict
    insertion order in ``metadata``.
    """
    payload = json.dumps(
        {"kind": kind, "content": content, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
