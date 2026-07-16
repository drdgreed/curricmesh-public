"""Deck render service — markdown ``deck.md`` → ``deck.{pdf,pptx,html}``.

Ported from CareerFoundry's ``slide_system_standard/scripts/build_module.sh``
(the proven Marp + Mermaid pipeline) into CurricMesh. This slice (S1) is the
de-risk-first foundation: render + store, no AI generation (that is S2, where
the quality risk lives).

Pipeline (per :func:`render_deck`):

1. Materialise the deck + any ``.mmd`` diagram sources into a temp workspace
   that mirrors the CareerFoundry layout (``slides/deck.md`` + ``diagrams/``),
   and drop the resolved theme CSS next to the deck.
2. Render each ``.mmd`` → retina PNG with **mermaid-cli** (``--scale 2``).
3. Render the deck → PDF, PPTX and HTML with **marp-cli** (``--allow-local-files``,
   ``--theme-set`` pointing at the ported career-forge theme).
4. Read the three artifacts back into memory as :class:`RenderedDeck`.

Node toolchain versions are PINNED (see ``versions.env`` in CareerFoundry) so a
rebuild reproduces shipped output. The subprocess boundary (:func:`_run`) is the
single seam CI mocks — real marp/mermaid need Node + Chromium and are far too
heavy for CI; the ``smoke_slide_render.py`` script exercises the real render
locally.
"""

from __future__ import annotations

import subprocess  # noqa: S404 — invoking the pinned marp/mermaid CLIs is the whole point
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.slides.themes import DEFAULT_THEME, load_theme

# ---------------------------------------------------------------------------
# Pinned toolchain (CareerFoundry slide_system_standard/scripts/versions.env)
# ---------------------------------------------------------------------------
# Bump deliberately; a change here can shift every deck. MARP is pinned to the
# version the shipped decks were built with; MERMAID to the current stable.
MARP_VERSION = "4.4.0"
MERMAID_VERSION = "11.16.0"

MARP_PKG = f"@marp-team/marp-cli@{MARP_VERSION}"
MERMAID_PKG = f"@mermaid-js/mermaid-cli@{MERMAID_VERSION}"

# Mermaid render knobs (Release Standard §3, "Render command (locked)").
_MERMAID_BG = "#0A0E1A"
_MERMAID_WIDTH = "2200"
_MERMAID_HEIGHT = "1000"
_MERMAID_SCALE = "2"  # retina-quality PNG

# npx binary — overridable for environments that vendor it elsewhere; tests mock
# the subprocess boundary entirely so this is never invoked under CI.
_NPX = "npx"


class RenderError(RuntimeError):
    """Raised when a marp/mermaid subprocess fails or an artifact is missing."""


@dataclass(frozen=True)
class RenderedDeck:
    """The three rendered artifacts, in memory, ready to store."""

    pdf: bytes
    pptx: bytes
    html: bytes


# ---------------------------------------------------------------------------
# Subprocess boundary (the single seam CI mocks)
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path) -> None:
    """Run a render subprocess, raising :class:`RenderError` on failure.

    Environment is inherited (so ``CHROME_PATH`` — which marp needs to locate
    headless Chromium — is honoured if the caller/host sets it, matching
    build_module.sh).
    """
    try:
        proc = subprocess.run(  # noqa: S603 — cmd is built from pinned constants + temp paths
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # npx/node not installed
        raise RenderError(
            f"render toolchain not found ({cmd[0]!r}); is Node.js installed? {exc}"
        ) from exc
    if proc.returncode != 0:
        raise RenderError(
            f"render command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Command builders (ported verbatim from build_module.sh — pure, unit-testable)
# ---------------------------------------------------------------------------


def _mermaid_cmd(src: Path, out: Path) -> list[str]:
    """`npx --yes @mermaid-js/mermaid-cli@X -i SRC -o OUT -b #0A0E1A --width 2200 --height 1000 --scale 2`"""
    return [
        _NPX, "--yes", MERMAID_PKG,
        "-i", str(src),
        "-o", str(out),
        "-b", _MERMAID_BG,
        "--width", _MERMAID_WIDTH,
        "--height", _MERMAID_HEIGHT,
        "--scale", _MERMAID_SCALE,
    ]


def _marp_cmd(deck: Path, out: Path, fmt: str, theme_css: Path) -> list[str]:
    """`npx --yes @marp-team/marp-cli@X DECK --{pdf|pptx|html} -o OUT --allow-local-files --theme-set THEME`"""
    return [
        _NPX, "--yes", MARP_PKG,
        str(deck),
        f"--{fmt}",
        "-o", str(out),
        "--allow-local-files",
        "--theme-set", str(theme_css),
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _normalize_frontmatter(deck_md: str) -> str:
    """Guarantee the deck opens with Marp YAML front-matter.

    Marp only recognizes front-matter when ``---`` is the *very first* line, and
    it **fails silently** otherwise — a leading BOM, blank line, or comment makes
    it drop the entire theme (cream background, navy code, title gradient) with
    no error, rendering an unstyled deck. We auto-strip a leading BOM and blank
    lines (a common, harmless slip) but raise loudly if real content precedes the
    front-matter, rather than emit a silently-broken deck.
    """
    stripped = deck_md.lstrip("﻿").lstrip("\n\r\t ")
    if stripped.split("\n", 1)[0].rstrip() != "---":
        preview = stripped[:60].replace("\n", "\\n")
        raise RenderError(
            "deck must begin with Marp YAML front-matter (`---` on line 1); "
            "content before it makes marp silently drop the theme. "
            f"Found instead: {preview!r}"
        )
    return stripped


def render_deck(
    deck_md: str,
    *,
    diagrams: dict[str, str] | None = None,
    theme: str = DEFAULT_THEME,
) -> RenderedDeck:
    """Render ``deck_md`` (+ optional mermaid ``diagrams``) to PDF, PPTX and HTML.

    - ``deck_md``  — the Marp markdown source. Reference diagrams as
      ``../diagrams/<name>.png`` (the CareerFoundry layout this reproduces).
    - ``diagrams`` — ``{name: mermaid_source}``. ``name`` may include a ``.mmd``
      suffix or not; it is rendered to ``../diagrams/<stem>.png``.
    - ``theme``    — a built-in theme name (default ``career-forge``). The deck's
      ``theme:`` frontmatter must match.

    Raises :class:`RenderError` if any render step fails or an artifact is
    missing. Raises ``FileNotFoundError`` for an unknown theme name.
    """
    deck_theme = load_theme(theme)
    diagrams = diagrams or {}

    with TemporaryDirectory(prefix="deck-render-") as tmp:
        root = Path(tmp)
        slides_dir = root / "slides"
        diagrams_dir = root / "diagrams"
        slides_dir.mkdir()
        diagrams_dir.mkdir()

        # Theme CSS lives next to the deck; passed to marp via --theme-set.
        theme_css = slides_dir / deck_theme.filename
        theme_css.write_text(deck_theme.css, encoding="utf-8")

        deck_path = slides_dir / "deck.md"
        deck_path.write_text(_normalize_frontmatter(deck_md), encoding="utf-8")

        # Step 1 — render diagrams (.mmd → retina .png).
        for name, source in diagrams.items():
            stem = name[:-4] if name.endswith(".mmd") else name
            mmd_path = diagrams_dir / f"{stem}.mmd"
            png_path = diagrams_dir / f"{stem}.png"
            mmd_path.write_text(source, encoding="utf-8")
            _run(_mermaid_cmd(mmd_path, png_path), cwd=root)

        # Step 2 — render deck (PDF / PPTX / HTML).
        out = {
            "pdf": slides_dir / "deck.pdf",
            "pptx": slides_dir / "deck.pptx",
            "html": slides_dir / "deck.html",
        }
        for fmt, target in out.items():
            _run(_marp_cmd(deck_path, target, fmt, theme_css), cwd=root)

        # Step 3 — read artifacts back into memory.
        artifacts: dict[str, bytes] = {}
        for fmt, target in out.items():
            if not target.is_file():
                raise RenderError(
                    f"marp reported success but {fmt} artifact is missing: {target.name}"
                )
            artifacts[fmt] = target.read_bytes()

    return RenderedDeck(pdf=artifacts["pdf"], pptx=artifacts["pptx"], html=artifacts["html"])
