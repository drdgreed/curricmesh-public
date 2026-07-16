"""Built-in deck themes for the slide render pipeline.

The locked ``career-forge`` theme (ported verbatim from CareerFoundry's Release
Standard v1.0.0) is the DEFAULT deck theme. It ships as a real Marp theme file
(``career_forge.css`` with a ``/* @theme career-forge */`` header) so the render
service can register it via ``marp --theme-set`` and a deck opts in with
``theme: career-forge`` in its frontmatter.

D-1 seam: ``get_deck_theme(tenant)`` is the single place a per-tenant theme will
override the default. Today every tenant resolves to ``career-forge``; a later
slice can look the tenant up and return a different ``DeckTheme`` without
touching the render service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_THEMES_DIR = Path(__file__).parent

# Name of the built-in default. Matches the `/* @theme career-forge */` header
# in career_forge.css AND the `theme:` value a deck sets in its frontmatter.
DEFAULT_THEME = "career-forge"


@dataclass(frozen=True)
class DeckTheme:
    """A resolved deck theme ready to hand to the render service.

    - ``name``     — the Marp theme name (deck frontmatter ``theme:`` must match).
    - ``css``      — the full theme CSS (written to the render temp dir).
    - ``filename`` — suggested on-disk filename for ``--theme-set``.
    """

    name: str
    css: str
    filename: str


def _theme_path(name: str) -> Path:
    """Resolve a theme name (e.g. ``career-forge``) to its CSS file on disk."""
    return _THEMES_DIR / f"{name.replace('-', '_')}.css"


def load_theme(name: str = DEFAULT_THEME) -> DeckTheme:
    """Load a built-in deck theme by name.

    Raises ``FileNotFoundError`` for an unknown theme name so a typo surfaces
    loudly instead of silently rendering an unstyled deck. This is the "render
    with a named theme" primitive the render service calls; tenant→theme
    resolution is ``get_deck_theme``'s job.
    """
    path = _theme_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"unknown deck theme: {name!r} (no {path.name})")
    return DeckTheme(name=name, css=path.read_text(encoding="utf-8"), filename=path.name)


def get_deck_theme(tenant: str | None = None) -> DeckTheme:  # noqa: ARG001
    """Return the deck theme for ``tenant``.

    The ``tenant`` argument is the D-1 seam: per-tenant theme overrides land
    here later. For now it is intentionally unused — every tenant resolves to
    the locked ``career-forge`` default so the seam exists without adding a
    lookup table we don't need yet (YAGNI).
    """
    return load_theme(DEFAULT_THEME)
