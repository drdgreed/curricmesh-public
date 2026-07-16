---
marp: true
theme: career-forge
paginate: true
header: "Widgets 101 · W1.1 — Assembling Synthetic Widgets"
footer: "example.com/widgets-101 | v1.0.0"
size: 16:9
style: |
  /* Minimal synthetic sample — see the built-in career-forge theme for the full styling. */
  section { font-family: 'Calibri', sans-serif; font-size: 22px; }

module_id: "w1-1-assembling-widgets"
module_number: "1.1"
deck_version: "1.0.0"
---

<!-- _class: title -->

# W1.1 — Assembling Synthetic Widgets

**A widget is assembled by selecting parts, joining them, and checking the result — repeat until it passes.**

🟦 This sample deck is synthetic placeholder content for the public mirror. It exists to exercise the render pipeline, not to teach anything real.

---

<!-- _class: wide-diagram -->

## The Assembly Loop

The loop is complete only when a failed check feeds back into part selection.

![The widget assembly loop: receive order, select parts, assemble, check, then ship or loop back to select parts.](../diagrams/tool_loop.png)

🟩 **Production tip:** every assembly loop needs a stop condition — a max-retry cap so a bad part can't loop forever.

<!-- _notes:
KEY CONCEPTS: the feedback edge (failed check → select parts) is what makes this a loop, not a pipeline.
REFERENCES: (synthetic sample — no external sources).
-->
