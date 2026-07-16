"""Tests for storing rendered decks in R2 (S1 — slide render pipeline).

``store_rendered_deck`` and the ``render_and_store_deck`` trigger are exercised
against ``FakeStorageBackend`` (no cloud). The render subprocess is mocked (via
the shared ``_FakeRun`` from test_render), so this proves the artifact-flow to
storage without Node/Chromium.
"""

from __future__ import annotations

from app.media.storage import FakeStorageBackend
from app.slides import render as render_mod
from app.slides.render import RenderedDeck
from app.slides.store import render_and_store_deck, store_rendered_deck

from tests.slides.test_render import DECK_MD, _FakeRun


def test_store_rendered_deck_puts_artifacts_and_returns_keys():
    storage = FakeStorageBackend()
    rendered = RenderedDeck(pdf=b"PDF", pptx=b"PPTX", html=b"HTML")

    keys = store_rendered_deck(storage, "decks/org/abc", rendered)

    assert keys == {
        "pdf_key": "decks/org/abc/deck.pdf",
        "pptx_key": "decks/org/abc/deck.pptx",
        "html_key": "decks/org/abc/deck.html",
    }
    # Bytes actually landed in storage under the decks/ prefix.
    assert storage.fetch("decks/org/abc/deck.pdf") == b"PDF"
    assert storage.fetch("decks/org/abc/deck.pptx") == b"PPTX"
    assert storage.fetch("decks/org/abc/deck.html") == b"HTML"


def test_store_rendered_deck_normalises_trailing_slash():
    storage = FakeStorageBackend()
    keys = store_rendered_deck(
        storage, "decks/org/abc/", RenderedDeck(pdf=b"a", pptx=b"b", html=b"c")
    )
    assert keys["pdf_key"] == "decks/org/abc/deck.pdf"  # no double slash


def test_render_and_store_returns_keys_and_presigned_urls(monkeypatch):
    monkeypatch.setattr(render_mod.subprocess, "run", _FakeRun())
    storage = FakeStorageBackend()

    result = render_and_store_deck(storage, "decks/org/xyz", DECK_MD, tenant="org")

    # Keys + a presigned URL per artifact; each URL embeds its key.
    for fmt in ("pdf", "pptx", "html"):
        key = result[f"{fmt}_key"]
        assert key == f"decks/org/xyz/deck.{fmt}"
        assert key in result[f"{fmt}_url"]
        # The (mocked) render output really reached storage.
        assert storage.head(key) is not None
