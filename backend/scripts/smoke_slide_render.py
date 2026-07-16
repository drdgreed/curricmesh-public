"""Live smoke test for the slide render pipeline — the REAL marp/mermaid render.

CI mocks the render subprocess (Node + Chromium are too heavy), so this script
is the counterpart that proves ACTUAL rendering, like ``smoke_media_r2.py`` does
for R2. Run it locally after installing the toolchain:

    npm install -g @marp-team/marp-cli@4.4.0 @mermaid-js/mermaid-cli@11.16.0
    # (a Chromium/Chrome must be resolvable — marp/mermaid drive it via puppeteer)

Usage (from backend/, venv active):
    python -m scripts.smoke_slide_render                 # render to ./slide_smoke_out
    python -m scripts.smoke_slide_render --out /tmp/deck # custom output dir
    python -m scripts.smoke_slide_render --check         # only report toolchain presence

It renders a tiny sample deck (with one mermaid diagram) through the SAME
``render_deck`` service the app uses, writes ``deck.{pdf,pptx,html}`` locally,
and prints PASS / FAIL. If the toolchain isn't installed it refuses gracefully
(SKIP, exit 0) rather than failing — the point is to verify a real render when
the tools are present.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from app.slides.render import MARP_VERSION, MERMAID_VERSION, RenderError, render_deck

# A minimal but real deck that exercises: the career-forge theme, the title
# class, a fenced code block (v3 brightness), and an embedded mermaid diagram.
_SAMPLE_DECK = """---
marp: true
theme: career-forge
paginate: true
size: 16:9
---

<!-- _class: title -->

# Slide Render Smoke

**CurricMesh S1 — marp + mermaid pipeline**

---

## A code block

```python
def hello() -> str:
    return "rendered by marp"
```

---

<!-- _class: wide-diagram -->

## A diagram

![loop](../diagrams/flow.png)
"""

_SAMPLE_DIAGRAM = """---
title: smoke flow
config:
  theme: base
---
flowchart LR
  A[Author deck.md] --> B[marp-cli]
  B --> C[PDF / PPTX / HTML]
"""


def _toolchain_present() -> bool:
    """True if the render CLIs are reachable (npx, and a Chrome for puppeteer)."""
    return shutil.which("npx") is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Real marp/mermaid render smoke test.")
    parser.add_argument(
        "--out",
        default="slide_smoke_out",
        help="Directory to write deck.{pdf,pptx,html} into (default: ./slide_smoke_out).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether the render toolchain is installed, then exit.",
    )
    args = parser.parse_args()

    print(f"Slide render smoke — marp@{MARP_VERSION} mermaid@{MERMAID_VERSION}")
    present = _toolchain_present()
    print(f"  npx on PATH: {'yes' if present else 'no'}")

    if args.check:
        return 0 if present else 1

    if not present:
        print(
            "  SKIP: npx not found — install Node + the pinned CLIs to run the real "
            "render (see this script's docstring). Refusing gracefully."
        )
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        rendered = render_deck(_SAMPLE_DECK, diagrams={"flow.mmd": _SAMPLE_DIAGRAM})
    except RenderError as exc:
        print(f"  FAIL: render raised RenderError:\n{exc}")
        return 1

    artifacts = {
        "deck.pdf": rendered.pdf,
        "deck.pptx": rendered.pptx,
        "deck.html": rendered.html,
    }
    for name, data in artifacts.items():
        if not data:
            print(f"  FAIL: {name} rendered to zero bytes")
            return 1
        (out_dir / name).write_bytes(data)
        print(f"  ✓ {name}: {len(data):,} bytes -> {out_dir / name}")

    print(f"\nPASS — real marp/mermaid render succeeded. Artifacts in {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
