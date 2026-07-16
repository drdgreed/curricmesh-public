"""Tests for the deck render service (S1 — slide render pipeline).

CI MOCKS the subprocess boundary — real marp/mermaid need Node + Chromium and
are far too heavy for CI (the ``smoke_slide_render.py`` script covers the real
render locally). These tests assert:

- ``render_deck`` invokes marp for PDF/PPTX/HTML with the PORTED args
  (``--allow-local-files``, ``--theme-set``, the PINNED marp version);
- mermaid runs per ``.mmd`` diagram with the locked retina scale (``--scale 2``);
- the rendered artifacts flow back as :class:`RenderedDeck` bytes;
- a failed subprocess and a missing artifact both surface a clear
  :class:`RenderError`;
- an unknown theme name raises ``FileNotFoundError``.

The deck fixture is CareerFoundry's ``deck_skeleton.md``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.slides import render as render_mod
from app.slides.render import (
    MARP_VERSION,
    MERMAID_VERSION,
    RenderedDeck,
    RenderError,
    render_deck,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "deck_skeleton.md"
DECK_MD = _FIXTURE.read_text(encoding="utf-8")


class _FakeRun:
    """Stand-in for ``subprocess.run`` that records calls and fakes artifacts.

    On each call it writes deterministic bytes to the ``-o <path>`` target (so
    ``render_deck`` can read the artifact back), unless configured to fail or to
    skip the write (to simulate a missing-artifact bug).
    """

    def __init__(self, *, fail_on: str | None = None, skip_write: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.skip_write = skip_write

    def __call__(self, cmd, cwd=None, capture_output=True, text=True, check=False):  # noqa: ARG002
        self.calls.append(cmd)
        if self.fail_on is not None and any(self.fail_on in part for part in cmd):
            return subprocess.CompletedProcess(cmd, 1, "", "marp: boom — bad deck frontmatter")
        if not self.skip_write:
            out = cmd[cmd.index("-o") + 1]
            Path(out).write_bytes(f"FAKE::{Path(out).name}".encode())
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    # Convenience filters over recorded calls.
    def marp_calls(self) -> list[list[str]]:
        return [c for c in self.calls if any("marp-cli" in p for p in c)]

    def mermaid_calls(self) -> list[list[str]]:
        return [c for c in self.calls if any("mermaid-cli" in p for p in c)]


@pytest.fixture
def fake_run(monkeypatch) -> _FakeRun:
    fake = _FakeRun()
    monkeypatch.setattr(render_mod.subprocess, "run", fake)
    return fake


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_render_deck_returns_three_artifacts(fake_run: _FakeRun):
    result = render_deck(DECK_MD)
    assert isinstance(result, RenderedDeck)
    # Bytes are what the (mocked) marp "wrote" for each format.
    assert result.pdf == b"FAKE::deck.pdf"
    assert result.pptx == b"FAKE::deck.pptx"
    assert result.html == b"FAKE::deck.html"


def test_render_deck_invokes_marp_for_each_format_with_ported_args(fake_run: _FakeRun):
    render_deck(DECK_MD)
    marp = fake_run.marp_calls()
    assert len(marp) == 3

    formats = set()
    for cmd in marp:
        # Pinned version is used (reproducible rebuilds).
        assert f"@marp-team/marp-cli@{MARP_VERSION}" in cmd
        assert MARP_VERSION == "4.4.0"
        # Local-file access (embedded diagram PNGs) + our ported theme.
        assert "--allow-local-files" in cmd
        assert "--theme-set" in cmd
        theme_arg = cmd[cmd.index("--theme-set") + 1]
        assert theme_arg.endswith("career_forge.css")
        # Exactly one of the three output format flags per invocation.
        fmt = {"--pdf", "--pptx", "--html"} & set(cmd)
        assert len(fmt) == 1
        formats |= fmt
    assert formats == {"--pdf", "--pptx", "--html"}


def test_render_deck_skips_mermaid_when_no_diagrams(fake_run: _FakeRun):
    render_deck(DECK_MD)
    assert fake_run.mermaid_calls() == []


def test_render_deck_renders_each_diagram_at_retina_scale(fake_run: _FakeRun):
    render_deck(
        DECK_MD,
        diagrams={"agent_loop.mmd": "flowchart LR\n A --> B", "arch": "graph TD\n X --> Y"},
    )
    mermaid = fake_run.mermaid_calls()
    assert len(mermaid) == 2
    for cmd in mermaid:
        assert f"@mermaid-js/mermaid-cli@{MERMAID_VERSION}" in cmd
        # Locked retina + dark-background knobs (Release Standard §3).
        assert "--scale" in cmd and cmd[cmd.index("--scale") + 1] == "2"
        assert "-b" in cmd and cmd[cmd.index("-b") + 1] == "#0A0E1A"
        assert "--width" in cmd and cmd[cmd.index("--width") + 1] == "2200"
        # Output is a PNG next to the deck (../diagrams/<stem>.png).
        out = cmd[cmd.index("-o") + 1]
        assert out.endswith(".png")
    # A `.mmd`-suffixed key and a bare key both resolve to <stem>.png.
    out_names = {Path(c[c.index("-o") + 1]).name for c in mermaid}
    assert out_names == {"agent_loop.png", "arch.png"}


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_render_failure_raises_clear_error(monkeypatch):
    fake = _FakeRun(fail_on="marp-cli")
    monkeypatch.setattr(render_mod.subprocess, "run", fake)
    with pytest.raises(RenderError) as exc:
        render_deck(DECK_MD)
    msg = str(exc.value)
    assert "exit 1" in msg
    assert "bad deck frontmatter" in msg  # stderr is surfaced


def test_missing_artifact_raises_render_error(monkeypatch):
    # Subprocess "succeeds" but writes nothing → the read-back guard must fire.
    fake = _FakeRun(skip_write=True)
    monkeypatch.setattr(render_mod.subprocess, "run", fake)
    with pytest.raises(RenderError) as exc:
        render_deck(DECK_MD)
    assert "artifact is missing" in str(exc.value)


def test_unknown_theme_raises(fake_run: _FakeRun):
    with pytest.raises(FileNotFoundError):
        render_deck(DECK_MD, theme="no-such-theme")


def test_toolchain_not_installed_raises_render_error(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("npx")

    monkeypatch.setattr(render_mod.subprocess, "run", _boom)
    with pytest.raises(RenderError) as exc:
        render_deck(DECK_MD)
    assert "Node.js" in str(exc.value)


# ---------------------------------------------------------------------------
# Front-matter guard — prevents marp's SILENT theme-drop (the render-loop bug)
# ---------------------------------------------------------------------------


def test_frontmatter_guard_rejects_content_before_frontmatter(fake_run: _FakeRun):
    """Content before the front-matter makes marp silently drop the theme — fail
    loud, and do NOT invoke marp on a silently-broken deck."""
    bad = "<!--\n docs wrapper above the front-matter\n-->\n" + DECK_MD
    with pytest.raises(RenderError, match="front-matter"):
        render_deck(bad)
    assert fake_run.marp_calls() == []


def test_frontmatter_guard_strips_leading_bom_and_blank_lines(fake_run: _FakeRun):
    """A leading BOM/blank line is a harmless slip — auto-stripped, then rendered."""
    result = render_deck("﻿\n\n" + DECK_MD)
    assert isinstance(result, RenderedDeck)
    assert len(fake_run.marp_calls()) == 3
