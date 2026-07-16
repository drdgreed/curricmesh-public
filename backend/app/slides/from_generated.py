"""Bridge a generated deck (S2) into the render pipeline (S1).

The deck generator (``app/ai/deckgen.py``) returns a :class:`GeneratedDeck` whose
``deck_markdown`` references each structural diagram as a Markdown image
``![alt](../diagrams/<stem>.png)`` and carries the matching Mermaid source in
``diagram_specs`` (one :class:`DeckDiagramSpec` per diagram, ``filename`` == the
image ref's stem).

``render_deck`` (S1) expects diagrams as a ``{stem: mermaid_source}`` dict, which
it renders to ``../diagrams/<stem>.png`` before rendering the deck. This module is
the small, pure adapter between the two:

    from app.slides.from_generated import diagrams_from_specs
    from app.slides.render import render_deck

    rendered = render_deck(
        generated.deck_markdown,
        diagrams=diagrams_from_specs(generated.diagram_specs),
    )

That is all the wiring the fix needs — a full publish/render hook that persists
the rendered artifacts is a later concern (S1's ``store.py`` already renders +
stores a deck; this only maps the generated diagrams into it).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.ai.schemas import DeckDiagramSpec


def diagrams_from_specs(specs: Iterable[DeckDiagramSpec]) -> dict[str, str]:
    """Map a deck's ``diagram_specs`` to the ``{stem: mermaid_source}`` dict
    :func:`app.slides.render.render_deck` renders to ``../diagrams/<stem>.png``.

    - The key is the spec's ``filename`` **stem** (any accidental ``.mmd``/``.png``
      suffix is stripped so it matches the ``../diagrams/<stem>.png`` image ref in
      the deck markdown). ``render_deck`` itself also tolerates a ``.mmd`` suffix,
      but we normalize here so the returned keys are exactly the image-ref stems.
    - Empty/whitespace-only Mermaid source or an empty filename is skipped — a
      spec with no renderable source would only produce an empty PNG.
    - A later spec with the same stem wins (last-write), matching dict semantics;
      a well-formed deck has one spec per stem.
    """
    diagrams: dict[str, str] = {}
    for spec in specs:
        stem = (spec.filename or "").strip()
        for suffix in (".mmd", ".png"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        source = (spec.mermaid or "").strip()
        if not stem or not source:
            continue
        diagrams[stem] = spec.mermaid
    return diagrams
