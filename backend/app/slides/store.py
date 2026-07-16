"""Persist rendered decks to object storage (R2) + the render→store trigger.

Reuses the existing ``StorageBackend`` adapter (``FakeStorageBackend`` in tests,
``S3StorageBackend``/R2 in prod) — no new storage machinery. Artifacts land under
a ``decks/<...>/`` key prefix; a ``DeckArtifact`` DB model is a LATER slice, so
S1 deliberately stores by key convention and adds no migration.
"""

from __future__ import annotations

from app.media.storage import StorageBackend
from app.slides.render import RenderedDeck, render_deck
from app.slides.themes import get_deck_theme

# Correct content types so a presigned GET serves each artifact with the right
# MIME (browsers render the HTML, download the PDF/PPTX).
_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "html": "text/html",
}


def store_rendered_deck(
    storage: StorageBackend, key_prefix: str, rendered: RenderedDeck
) -> dict[str, str]:
    """Upload the three artifacts under ``key_prefix`` and return their keys.

    Returns ``{"pdf_key": ..., "pptx_key": ..., "html_key": ...}``. Keys are
    ``<key_prefix>/deck.<ext>``; the caller is responsible for making
    ``key_prefix`` unique + tenant-scoped (e.g. ``decks/<org>/<deck-id>``).
    """
    prefix = key_prefix.rstrip("/")
    keys: dict[str, str] = {}
    for fmt, data in (("pdf", rendered.pdf), ("pptx", rendered.pptx), ("html", rendered.html)):
        key = f"{prefix}/deck.{fmt}"
        storage.put_bytes(key, data, _CONTENT_TYPES[fmt])
        keys[f"{fmt}_key"] = key
    return keys


def render_and_store_deck(
    storage: StorageBackend,
    key_prefix: str,
    deck_md: str,
    *,
    diagrams: dict[str, str] | None = None,
    tenant: str | None = None,
) -> dict[str, str]:
    """Render ``deck_md`` and store the artifacts; return keys + presigned URLs.

    This is the S1 render→store trigger (the admin endpoint wraps it). The
    ``tenant`` selects the theme via ``get_deck_theme`` (D-1 seam — today always
    career-forge). Blocking (shells out to marp/mermaid); the endpoint offloads
    it to a threadpool. Raises ``RenderError`` if rendering fails.
    """
    theme = get_deck_theme(tenant)
    rendered = render_deck(deck_md, diagrams=diagrams, theme=theme.name)
    keys = store_rendered_deck(storage, key_prefix, rendered)
    return {
        **keys,
        "pdf_url": storage.presigned_get_url(keys["pdf_key"]),
        "pptx_url": storage.presigned_get_url(keys["pptx_key"]),
        "html_url": storage.presigned_get_url(keys["html_key"]),
    }
