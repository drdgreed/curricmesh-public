"""Tests for the built-in deck theme resource (S1 — slide render pipeline).

The locked ``career-forge`` theme is ported verbatim from CareerFoundry's
Release Standard v1.0.0. These tests pin the load path, the Marp-theme contract
(``/* @theme career-forge */`` header + a name that a deck's ``theme:`` matches),
the D-1 per-tenant seam, and a handful of the LOCKED palette values so a silent
theme regression fails CI.
"""

from __future__ import annotations

from app.slides.themes import DEFAULT_THEME, DeckTheme, get_deck_theme


def test_default_theme_is_career_forge():
    assert DEFAULT_THEME == "career-forge"


def test_get_deck_theme_returns_deck_theme():
    theme = get_deck_theme()
    assert isinstance(theme, DeckTheme)
    assert theme.name == "career-forge"
    assert theme.filename == "career_forge.css"
    assert theme.css.strip()


def test_theme_declares_marp_theme_header():
    # Marp registers a theme by the `/* @theme <name> */` header; the name here
    # must equal what a deck sets in `theme:` frontmatter.
    css = get_deck_theme().css
    assert "/* @theme career-forge */" in css
    # `@import 'default'` gives pagination + base layout the locked CSS builds on.
    assert "@import 'default'" in css


def test_theme_pins_locked_palette_values():
    css = get_deck_theme().css
    # Slide base (Release Standard §2).
    assert "background: #F7F6F2" in css
    assert "color: #1F1D17" in css
    # Code-block v3 brightness lock.
    assert "#07091a" in css
    assert "JetBrains Mono" in css
    assert "#7FF0FF" in css  # keyword token
    # Heading teal + title hero.
    assert "#01696F" in css
    assert "section.title" in css
    # Locked callout classes.
    for callout in (
        "callout-concept",
        "callout-gotcha",
        "callout-anti",
        "callout-prod",
        "callout-hiring",
    ):
        assert callout in css
    # Locked layout classes.
    assert "section.dense" in css
    assert "section.wide-diagram" in css


def test_tenant_seam_falls_back_to_default_for_now():
    # D-1 seam: an unknown tenant resolves to the locked default today. When
    # per-tenant themes land, this test documents the fallback contract.
    assert get_deck_theme("some-tenant").name == "career-forge"
    assert get_deck_theme(None).css == get_deck_theme("other").css
