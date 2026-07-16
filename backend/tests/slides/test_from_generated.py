"""Generate -> render bridge: ``diagrams_from_specs`` (pure, no I/O).

Proves the adapter that maps a generated deck's ``diagram_specs`` (Mermaid source
+ filename stem) into the ``{stem: mermaid_source}`` dict ``render_deck`` expects,
so a generated deck's PNG image refs resolve to real rendered diagrams.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.ai.schemas import DeckDiagramSpec
from app.slides import render as render_mod
from app.slides.from_generated import diagrams_from_specs
from app.slides.render import RenderedDeck, render_deck


def _spec(filename: str, mermaid: str = "flowchart LR\n  A --> B") -> DeckDiagramSpec:
    return DeckDiagramSpec(filename=filename, mermaid=mermaid, alt_text="alt")


def test_maps_specs_to_stem_source_dict():
    specs = [
        _spec("agent_loop", "flowchart LR\n  P --> R"),
        _spec("architecture", "flowchart LR\n  X --> Y"),
    ]
    out = diagrams_from_specs(specs)
    # Keyed by the exact image-ref stem the deck references (../diagrams/<stem>.png).
    assert out == {
        "agent_loop": "flowchart LR\n  P --> R",
        "architecture": "flowchart LR\n  X --> Y",
    }


def test_empty_specs_yield_empty_dict():
    assert diagrams_from_specs([]) == {}


def test_filename_suffix_is_stripped_to_the_image_ref_stem():
    # A model that slips a .mmd/.png suffix into filename still resolves to the
    # bare stem the deck's ../diagrams/<stem>.png ref uses.
    out = diagrams_from_specs([_spec("tool_loop.mmd"), _spec("flow.png")])
    assert set(out) == {"tool_loop", "flow"}


def test_blank_source_or_name_is_skipped():
    out = diagrams_from_specs([
        _spec("has_source", "flowchart LR\n A --> B"),
        _spec("blank_source", "   "),
        _spec("", "flowchart LR\n C --> D"),
    ])
    assert set(out) == {"has_source"}


def test_source_is_passed_through_verbatim():
    src = "flowchart LR\n  A[Start] -->|edge| B[End]\n"
    out = diagrams_from_specs([_spec("d", src)])
    assert out["d"] == src  # render_deck writes this verbatim into <stem>.mmd


# ---------------------------------------------------------------------------
# generate -> render (mocked subprocess): the mermaid -> PNG step runs for the
# spec'd diagram, so a generated deck's PNG image ref resolves to a real render.
# ---------------------------------------------------------------------------


class _FakeRun:
    """Records subprocess calls and fakes each ``-o`` artifact (mirrors test_render)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, capture_output=True, text=True, check=False):  # noqa: ARG002
        self.calls.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_bytes(f"FAKE::{Path(out).name}".encode())
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    def mermaid_out_names(self) -> set[str]:
        return {
            Path(c[c.index("-o") + 1]).name
            for c in self.calls
            if any("mermaid-cli" in p for p in c)
        }


_DECK_WITH_IMAGE_REF = (
    "---\n"
    "marp: true\n"
    "theme: career-forge\n"
    "---\n\n"
    "<!-- _class: wide-diagram -->\n\n"
    "## Worked Example\n\n"
    "![The agent loop](../diagrams/agent_loop.png)\n"
)


def test_generated_deck_renders_its_spec_diagram_to_png(monkeypatch):
    """render_deck(deck_md, diagrams=diagrams_from_specs(specs)) runs mermaid for
    the spec'd diagram, producing the ../diagrams/<stem>.png the deck references."""
    fake = _FakeRun()
    monkeypatch.setattr(render_mod.subprocess, "run", fake)

    specs = [_spec("agent_loop", "flowchart LR\n  P --> R")]
    result = render_deck(_DECK_WITH_IMAGE_REF, diagrams=diagrams_from_specs(specs))

    assert isinstance(result, RenderedDeck)
    # The mermaid step ran once, writing exactly the PNG stem the deck's ref uses.
    assert fake.mermaid_out_names() == {"agent_loop.png"}


def test_committed_sample_deck_is_renderable_with_its_committed_mmd(monkeypatch):
    """The committed sample renders AS-IS: front-matter is line 1 (guard passes),
    the worked-example slide uses a PNG image ref (not inline mermaid), and its
    committed .mmd source renders to the PNG the deck references."""
    repo_root = Path(__file__).resolve().parents[3]
    sample = repo_root / "docs" / "slides" / "sample_generated_deck.md"
    mmd = repo_root / "docs" / "slides" / "diagrams" / "tool_loop.mmd"
    md = sample.read_text(encoding="utf-8")

    # The FIX: image ref, no inline mermaid (Marp renders inline mermaid as raw code).
    assert "](../diagrams/tool_loop.png)" in md
    assert "```mermaid" not in md

    fake = _FakeRun()
    monkeypatch.setattr(render_mod.subprocess, "run", fake)
    # Front-matter guard passes (no leading comment) and the diagram renders.
    result = render_deck(md, diagrams={"tool_loop": mmd.read_text(encoding="utf-8")})
    assert isinstance(result, RenderedDeck)
    assert "tool_loop.png" in fake.mermaid_out_names()
