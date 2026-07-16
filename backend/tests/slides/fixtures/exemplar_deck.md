---
marp: true
theme: career-forge
paginate: true
header: "Widgets 101 · W1.1 — Assembling Synthetic Widgets"
footer: "example.com | v1.0.0 | 2026-06-14"
size: 16:9
style: |
  /* === Career-Forge slide template v1.1 === */
  /* v1.1 fixes: tighter body font, code contrast, safe-area padding so footer never overlaps content. */
  section {
    font-family: 'Calibri', 'Trebuchet MS', sans-serif;
    font-size: 22px;
    line-height: 1.4;
    color: #1F1D17;
    background: #F7F6F2;
    padding: 50px 60px 70px 60px;   /* extra bottom padding keeps footer clear */
    overflow: hidden;
  }
  /* Inline code: dark background, near-white text for high contrast on light slides */
  section code {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.85em;
    background: #1C1B19;
    color: #F2F0EA;
    padding: 1px 6px;
    border-radius: 3px;
  }
  /* Fenced code blocks: dark theme block with proper padding & white text */
  section pre {
    background: #1C1B19;
    border-radius: 6px;
    padding: 14px 18px;
    margin: 8px 0;
    overflow: hidden;
  }
  section pre code {
    background: transparent;
    color: #F2F0EA;
    font-size: 17px;
    line-height: 1.35;
    padding: 0;
  }
  section pre code .hljs-keyword,
  section pre code .hljs-built_in { color: #7DD3FC; }
  section pre code .hljs-string { color: #FCD34D; }
  section pre code .hljs-comment { color: #94A3B8; font-style: italic; }
  section pre code .hljs-number { color: #FCA5A5; }
  /* Headings */
  section h1 { font-size: 38px; color: #01696F; margin: 0 0 16px 0; }
  section h2 { font-size: 28px; color: #0C4E54; margin: 0 0 14px 0; line-height: 1.2; }
  section h3 { font-size: 22px; color: #0C4E54; margin: 12px 0 8px 0; }
  /* Tables: smaller font, tighter cells, no horizontal overflow */
  section table {
    font-size: 18px;
    line-height: 1.3;
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
  }
  section th, section td {
    border: 1px solid #D6D2C4;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
  }
  section th { background: #EFEBDC; color: #0C4E54; }
  /* Lists tighten */
  section ul, section ol { margin: 6px 0 6px 24px; padding: 0; }
  section li { margin: 3px 0; }
  /* Blockquotes */
  section blockquote {
    border-left: 3px solid #0C4E54;
    margin: 8px 0;
    padding: 4px 0 4px 14px;
    color: #4A4636;
    font-style: italic;
    font-size: 0.95em;
  }
  /* Callouts */
  section .callout-concept  { border-left: 4px solid #01696F; padding-left: 1em; }
  section .callout-gotcha   { border-left: 4px solid #D19900; padding-left: 1em; }
  section .callout-anti     { border-left: 4px solid #A13544; padding-left: 1em; }
  section .callout-prod     { border-left: 4px solid #437A22; padding-left: 1em; }
  section .callout-hiring   { border-left: 4px solid #7A39BB; padding-left: 1em; }
  /* Header & footer: smaller, faded, won't collide visually */
  section header {
    font-size: 14px;
    color: #6B6452;
    top: 18px;
  }
  section footer {
    font-size: 13px;
    color: #8A8472;
    bottom: 18px;
  }
  /* Pagination */
  section::after {
    font-size: 13px;
    color: #8A8472;
  }
  /* Dense-slide class for code-heavy slides — shrinks code & lists further */
  section.dense { font-size: 19px; padding: 40px 56px 60px 56px; }
  section.dense pre code { font-size: 15px; line-height: 1.3; }
  section.dense h2 { font-size: 26px; }
  /* Title-slide class — for slide 1 hero */
  section.title {
    background: linear-gradient(135deg, #01696F 0%, #0C4E54 100%);
    color: #F7F6F2;
    padding: 80px 80px;
  }
  section.title h1 { color: #F7F6F2; font-size: 54px; }
  section.title h2 { color: #D4C99E; font-size: 28px; }

# CurricMesh version-pin (populated by build system)
curricmesh_lineage_key: "w1-1-assembling-widgets/slides"
curricmesh_content_hash: "sha256:PENDING"
curricmesh_version: "v1.0.0"

# Slide system metadata (synthetic placeholder course)
module_id: "w1-1-assembling-widgets"
module_number: "1.1"
deck_version: "1.0.0"
target_bloom_ceiling: "apply"
prereq_remedial:
  - "widgets/hand-tools-basics"      # status: planned — placeholder remedial
estimated_slides: 24
---

<!-- ACT 1: HOOK & ANCHOR — Slides 1–4 -->

<!-- _class: title -->
<!-- _header: "" _footer: "" -->
# W1.1 — Assembling Synthetic Widgets

**A finished widget is just three fictional parts snapped together in the right order: sprocket → flange → gizmo.**

🟪 **Placeholder anchor:** "Widget assembly" is the make-believe skill this synthetic module pretends to teach. None of this is real — it exists only to exercise the deck pipeline. This module builds on the single parts you met in W0 and clips them into one working widget.

<!-- _notes:
(1) KEY CONCEPTS: Welcome to the synthetic Widgets 101 course. This deck is placeholder content that exercises the render and QA pipeline — nothing here describes a real product. The through-line is simple: a widget is three fictional parts (a sprocket, a flange, and a gizmo) assembled in order. W0 introduced each part on its own; this module snaps them together into one widget.

(2) EMPHASIZE: Order matters in the make-believe assembly — sprocket first, then flange, then gizmo. Everything downstream assumes that order.

(3) REFERENCES:
- [Widgets 101 course outline](https://example.com/widgets-101) synthetic module map
- [W0 recap — widget parts](https://example.com/widgets-101/w0) the three fictional parts
-->

---

<!-- _header: "W1.1 · Slide Conventions" -->

## How to Read These Slides — Conventions

Every deck in this synthetic course uses the same conventions:

**Color legend (in callouts and tags):**

- 🟦 **Concept** — the mental model this slide installs
- 🟪 **Placeholder anchor** — a made-up "why it matters" tag
- 🟨 **Gotcha** — a common (fictional) mistake to avoid
- 🟩 **Production tip** — how the pretend workshop does it at scale
- 🟥 **Anti-pattern** — a design to actively avoid

**Speaker notes are part of the material.** The notes block under each slide (`KEY CONCEPTS / EMPHASIZE / REFERENCES`) carries the reasoning and the (synthetic) citations.

**Code blocks** use a high-contrast palette (near-white on deep navy) tuned for projectors.

<!-- _notes:
(1) KEY CONCEPTS: This slide orients the reader to the shared visual language. Blue is a concept, purple a placeholder anchor, yellow a gotcha, green a production tip, red an anti-pattern. The colors recur on every slide.

(2) EMPHASIZE: The callout colors are a fixed vocabulary — red always means "avoid this."

(3) REFERENCES:
- [Career-Forge slide template v1.1](https://example.com/widgets-101/template) callout legend definitions
-->

---

## The Problem You Will Solve

A work order lands on the bench: *"Assemble one widget from a loose sprocket, flange, and gizmo."* Picking parts at random does not work — a widget only holds together when the parts clip in the correct sequence.

- Grab the gizmo first and it **has nothing to seat against** — the flange is not in place yet.
- Clip the flange before the sprocket and the sprocket **cannot reach its slot**.
- Skip the seating check and the widget **rattles apart** on the first pretend-test.

**A tidy assembly routine fixes this:** seat the sprocket, clip the flange to it, snap the gizmo on top, then run the seating check.

🟦 **By the end of this module you can:** name the three widget parts, put them together in the correct order, and run the seating check that says the (fictional) widget is done.

<!-- _notes:
(1) KEY CONCEPTS: This is the concrete motivation. The three failure modes each map to a rule: seat the sprocket first (nothing to build against otherwise), clip the flange second (the sprocket needs a home first), snap the gizmo last, then verify with the seating check. All fictional — the point is a clean, ordered routine.

(2) EMPHASIZE: The order is the whole lesson: sprocket → flange → gizmo → check.

(3) REFERENCES:
- [Synthetic assembly guide](https://example.com/widgets-101/assembly) the ordered routine
-->

---

## Why This Module → Placeholder Anchor

| Skill | "Score" | "Demand" | W1.1 coverage |
|-------|---------|----------|---------------|
| Widget assembly (skill-100) | **3** | **High** | Built from parts in the lab |
| Part identification | 3 | High | Recap from W0 |
| Seating verification (downstream) | 3 | High | Foundation for W1.2 |

🟩 **Production tip:** Every pretend assembly needs a seating check. Without a done-condition you do not have a widget — you have a loose pile of parts.

🟪 **Placeholder hook:** the synthetic "widget-maker" role card lists "assemble a widget end to end" — the routine you build here is the literal answer to that made-up requirement.

<!-- _notes:
(1) KEY CONCEPTS: Fictional demand framing. Widget assembly (skill-100) is scored 3 because this module produces a make-believe portfolio artifact: an assembled widget. Part identification is a W0 recap; seating verification carries into W1.2.

(2) EMPHASIZE: The seating check is the line between a demo and a "finished" widget.

(3) REFERENCES:
- [Synthetic skills matrix](https://example.com/widgets-101/skills) skill-100 = 3
-->

---

<!-- ACT 2: PREREQS & BRIDGE — Slides 5–7 -->

## Before We Begin — What You Need

**You should already own (from W0):**

- **The sprocket (W0.1):** you can recognise a sprocket and its keyed slot.
- **The flange (W0.1):** you know which face of the flange clips to a sprocket.
- **The gizmo (W0.2):** you have seen a gizmo seated on a flange.
- **The seating check (W0.2):** you have run the pretend rattle-test once.

🟨 **Gotcha — mixed part sizes:** the bench kit ships two sprocket sizes. If the small sprocket keeps slipping, swap to the large one before the flange step.

<!-- _notes:
(1) KEY CONCEPTS: W1.1 builds directly on W0. The four prerequisites are the parts and the check you assemble here. The sprocket and flange come from W0.1; the gizmo and the seating check from W0.2.

(2) EMPHASIZE: These are gates, not suggestions — if the single-part recognition from W0 is shaky, re-run it first.

(3) REFERENCES:
- [W0.1 — sprocket & flange](https://example.com/widgets-101/w0-1) the two base parts
- [W0.2 — gizmo & seating check](https://example.com/widgets-101/w0-2) the top part and the test
-->

---

## Bridge — Where W1.1 Fits in the Course

The course arc: **Parts → Assembly → Finishing → Inspection**

```text
W0 Widget Parts   ──►   W1.1 Assembly (you are here)   ──►   W1.2 Finishing
   single parts         sprocket → flange → gizmo             paint & polish
```

W0 gave you the parts one at a time. **W1.1 snaps them into one widget** — the routine every later module assumes.

🟦 **Connecting concept:** W1.2 (Finishing) solves assembly's blind spot — a freshly assembled widget is bare metal and still needs paint and polish before inspection.

<!-- _notes:
(1) KEY CONCEPTS: This locates W1.1 in the four-stage arc: Parts, Assembly, Finishing, Inspection. W0 sits in Parts; W1.1 begins Assembly by clipping the single parts into a widget; W1.2 then finishes it.

ALT TEXT: A linear diagram. "W0 Widget Parts (single parts)" points right to "W1.1 Assembly (you are here), sprocket → flange → gizmo," which points right to "W1.2 Finishing (paint & polish)."

(2) EMPHASIZE: W1.1 is the hinge from loose parts to a working widget.

(3) REFERENCES:
- [W1.2 — finishing a widget](https://example.com/widgets-101/w1-2) the next module
-->

---

## New Terms in This Module

| Term | Definition |
|------|-----------|
| **Widget** | A finished assembly of a sprocket, a flange, and a gizmo (all fictional). |
| **Sprocket** | The base part with a keyed slot; seats first. |
| **Flange** | The middle part; clips onto the sprocket. |
| **Gizmo** | The top part; snaps onto the flange. |
| **Seating check** | The pretend rattle-test that confirms the widget is assembled. |
| **`DONE`** | The status the routine returns once the seating check passes. |

🟦 **Pre-teaching:** these six terms recur on every slide that follows. Meeting them now keeps them from adding load mid-explanation.

<!-- _notes:
(1) KEY CONCEPTS: Six recurring terms, introduced before they are needed. The widget is the whole assembly; the sprocket, flange, and gizmo are the three parts in seating order; the seating check is the verifier; DONE is the status the routine returns when the check passes.

(2) EMPHASIZE: Three terms are parts (sprocket, flange, gizmo); two are the verifier and its status (seating check, DONE).

(3) REFERENCES:
- [Widget glossary](https://example.com/widgets-101/glossary) the six terms
-->

---

<!-- ACT 3: CONCEPT BUILD-UP — Slides 8–13 -->

<!-- _class: wide-diagram -->

## Concept 1 — The Assembled Widget

A widget is complete only when each part seats against the one below it. That downward seating is what makes it a widget and not a loose pile.

![Widget assembly diagram — sprocket at the base, flange clipped on top, gizmo snapped on top of the flange](../diagrams/widget_assembly.png)

🟦 **Concept:** the assembly order is the substrate — every technique in this module (keyed slots, clip tabs, snap fit) rides on top of the sprocket → flange → gizmo order.

<!-- _notes:
(1) KEY CONCEPTS: Concept 1 is the widget itself, drawn as a stack. The three parts are ordinary; the critical detail is that each part seats against the one below. That seating is what distinguishes a widget from a pile.

ALT TEXT: A stacked diagram — a sprocket at the base, a flange clipped onto it, and a gizmo snapped onto the flange, with downward arrows showing each part seating against the one below.

(2) EMPHASIZE: The seating order is the whole idea. Remove it and you have parts that fall apart.

(3) REFERENCES:
- [Synthetic assembly guide](https://example.com/widgets-101/assembly) the seating order
-->

---

<!-- _class: dense -->

## Concept 2 — The Sprocket Seats First

The sprocket carries a keyed slot. The routine records it as the base of the widget. In our bench kit each part is one small dictionary:

```python
# One sprocket — the base part, keyed slot noted
sprocket = {"part": "sprocket", "slot": "keyed", "seated": True}

# The routine seats it as the base before anything clips on
widget = {"base": sprocket, "status": "assembling"}
```

Only once the sprocket reports `seated: True` does the flange step begin — bench feedback, not guesswork.

🟩 **Production tip:** log every seated part. The seated log is the first thing you check when a widget rattles.

<!-- _notes:
(1) KEY CONCEPTS: Concept 2 is the sprocket — the base. It carries a keyed slot and is recorded as the widget's base. Each part is a small dict; the sprocket seats first, and only when it reports seated does the flange step begin.

(2) EMPHASIZE: The seated log is the primary debugging artifact when a widget fails its check.

(3) REFERENCES:
- [Sprocket spec (synthetic)](https://example.com/widgets-101/sprocket) keyed slot and seating
-->

---

<!-- _class: dense -->

## Concept 3 — The Flange Clips On

The flange is described by a tiny record that names which sprocket it clips to:

```
{ "part": "flange", "clips_to": "sprocket", "tabs": 4, "seated": false }
```

The routine flips `seated` to `true` once all four clip tabs engage the sprocket. A flange with fewer than four engaged tabs is not seated.

🟨 **Gotcha — half-clipped flange:** three of four tabs feels tight but fails the seating check. Always confirm all four before the gizmo step.

<!-- _notes:
(1) KEY CONCEPTS: Concept 3 is the flange — the middle part. It is a small record naming the sprocket it clips to and its four clip tabs. It is seated only when all four tabs engage.

(2) EMPHASIZE: All four tabs, every time — a half-clipped flange passes by feel but fails the check.

(3) REFERENCES:
- [Flange spec (synthetic)](https://example.com/widgets-101/flange) clip tabs and seating
-->

---

<!-- _class: dense -->

## Concept 4 — The Gizmo Snaps Last

The gizmo is the top part. It snaps onto a seated flange and gives the widget its (fictional) function.

- **Seat order:** sprocket → flange → **gizmo** — never before the flange is seated.
- The gizmo can be the standard cap (cheaper) or the deluxe cap (when the pretend spec demands it).
- **Measured effect (homework):** the deluxe gizmo improves the make-believe score — but adds weight and cost.

🟨 **Gotcha — gizmo before flange snaps nothing:** with no seated flange under it, the gizmo has no lip to grip and pops straight off.

<!-- _notes:
(1) KEY CONCEPTS: Concept 4 is the gizmo — the top part. It snaps onto a seated flange and gives the widget its fictional function. Standard cap is cheaper; deluxe when the spec asks.

(2) EMPHASIZE: The gizmo is always last. No seated flange, nothing to grip.

(3) REFERENCES:
- [Gizmo spec (synthetic)](https://example.com/widgets-101/gizmo) standard vs deluxe cap
-->

---

<!-- _class: dense -->

## Concept 5 — When NOT to Add a Part

Every extra part adds weight, cost, and one more thing that can rattle loose. A plain three-part widget beats a fancier one whenever the plain one meets the spec.

> "Add a part only where the spec actually reaches." — synthetic bench guidance

**The test:** does the make-believe spec require this part? If not, leave it off. Extra parts earn their keep only when the widget genuinely needs them.

🟥 **Anti-pattern — Everything-Bolted-On:** clipping spare flanges and a second gizmo onto a widget "because more parts look impressive." It adds weight and rattle with zero benefit. A three-part widget is a finished widget.

<!-- _notes:
(1) KEY CONCEPTS: Concept 5 is the counterweight: when NOT to add a part. Extra parts add weight, cost, and rattle risk. If the spec does not call for a part, leave it off.

(2) EMPHASIZE: The test is one question — does the spec require this part? If not, it is not a widget improvement.

(3) REFERENCES:
- [Synthetic bench guidance](https://example.com/widgets-101/bench) minimal parts
-->

---

<!-- _class: wide-diagram dense -->

## Concept 6 — The Seating Check

Every widget needs a done-condition, or you ship a rattling pile. The routine checks each exit below before it returns `DONE`.

![Seating check diagram — the routine verifies sprocket seated, flange four tabs engaged, gizmo snapped, and no rattle before returning DONE](../diagrams/seating_check.png)

🟩 **Bench minimum:** confirm the **sprocket seated**, the **flange's four tabs**, and the **gizmo snapped**. For a full check add a **rattle test** and a **weight check**.

<!-- _notes:
(1) KEY CONCEPTS: Concept 6 is the seating check — the done-condition. It verifies each part seated and returns DONE only when all pass. Minimum: sprocket seated, four flange tabs, gizmo snapped. Full: add a rattle test and a weight check.

ALT TEXT: A flow diagram of one seating check with exit conditions: sprocket seated, flange four tabs engaged, gizmo snapped, rattle test passed, weight within spec — all leading to a DONE terminal.

(2) EMPHASIZE: Three checks are mandatory; two more harden it.

(3) REFERENCES:
- [Seating check spec (synthetic)](https://example.com/widgets-101/check) the done-condition
-->

---

<!-- ACT 4: FORMATIVE CHECK #1 — Slide 14 (Understand level) -->

## Check Your Mental Model — Before We Assemble

**Formative Check 1 of 2 | Level: Understand | ~8 minutes**

This is not a grade. It is a mirror. Answer confidently and you are ready for the worked example.

**Question preview:** A bench mate seats a gizmo directly onto a bare sprocket with no flange, and a second mate insists on adding a fourth spare part "for looks." Which move is correct — and why?

- A) Both — more parts and any order are fine
- B) The gizmo-first move — the gizmo is the important part
- C) Neither — the gizmo needs a seated flange first, and the spare part is unnecessary
- D) The spare part — extra parts always help

🟩 **Take the full check on the synthetic platform — it grades you live and explains every distractor.**

> Open Check 1 (Understand) — multiple-choice + short answer · ~8 min
> Live grading. Immediate feedback on every distractor.

<!-- _notes:
(1) KEY CONCEPTS: Pause here before the worked example. The scenario contrasts a wrong order (gizmo on a bare sprocket) with an unnecessary spare part. The correct answer is C: the gizmo needs a seated flange first, and the spare part is not called for by the spec.

(2) EMPHASIZE: This check is formative — it catches the Everything-Bolted-On habit before it becomes a rattling widget.

(3) REFERENCES:
- [Understand-level check (synthetic)](https://example.com/widgets-101/check-1) distractor explanations
-->

---

<!-- ACT 5: WORKED EXAMPLE + LIVE CODE — Slides 15–19 -->

<!-- _class: wide-diagram -->

## Worked Example — Assemble One Widget

**Goal:** assemble a single widget from a loose sprocket, flange, and gizmo — seat the sprocket, clip the flange, snap the gizmo, then return `DONE`.

![Widget assembly diagram — the sprocket, flange, and gizmo mapped to the seat, clip, snap, and check steps of the routine](../diagrams/widget_assembly.png)

🟦 **Concept:** every step on this diagram maps to a few lines of Python you will read next — seat, clip, snap, and the final check.

<!-- _notes:
(1) KEY CONCEPTS: This worked example assembles one widget end to end. Given a loose sprocket, flange, and gizmo, the routine seats the sprocket, clips the flange, snaps the gizmo, and returns DONE. The diagram is the same assembly stack, annotated as the blueprint for the code.

ALT TEXT: The widget assembly stack annotated for the worked example — each part labeled with the routine step it maps to: seat (sprocket), clip (flange), snap (gizmo), and a final seating check that returns DONE.

(2) EMPHASIZE: The diagram is the code map — seat, clip, snap, check.

(3) REFERENCES:
- [Worked example (synthetic)](https://example.com/widgets-101/worked-example) the reference routine
-->

---

<!-- _class: dense -->

## Code Walk — The Assembly Routine

```python
# widgets_lab/assemble.py — the assembly routine, every step explicit
MAX_TABS = 4

def assemble(sprocket: dict, flange: dict, gizmo: dict) -> dict:
    widget = {"parts": [], "status": "assembling"}
    widget["parts"].append(seat(sprocket))            # 1. seat the base
    if flange["tabs"] < MAX_TABS:                      # 2. flange check
        return {"status": "ABORTED: flange not fully clipped"}
    widget["parts"].append(clip(flange))              # 3. clip the middle
    widget["parts"].append(snap(gizmo))               # 4. snap the top
    widget["status"] = "DONE" if seating_check(widget) else "RATTLES"
    return widget
```

🟩 **Production tip:** the `parts` list IS the widget's record — every seated part appended is evidence the seating check reads.

<!-- _notes:
(1) KEY CONCEPTS: This is the heart of the module — the assembly routine with every step explicit. Step 1 seats the sprocket as the base. Step 2 refuses to continue if the flange has fewer than four tabs. Step 3 clips the flange. Step 4 snaps the gizmo. Then the seating check decides DONE vs RATTLES.

(2) EMPHASIZE: The parts list IS the widget's record — the seating check reads exactly what you appended.

(3) REFERENCES:
- [Assembly routine (synthetic)](https://example.com/widgets-101/assemble) the routine structure
-->

---

<!-- _class: dense -->

## Code Walk — The Parts Registry

```python
# widgets_lab/parts.py — a tiny registry; deterministic for tests
PARTS = {}

def part(fn):                    # decorator: register a part builder by name
    PARTS[fn.__name__] = fn
    return fn

@part
def sprocket(size: str = "large") -> dict:
    return {"part": "sprocket", "slot": "keyed", "size": size, "seated": True}

@part
def flange(tabs: int = 4) -> dict:   # four tabs = fully clipped
    return {"part": "flange", "clips_to": "sprocket", "tabs": tabs, "seated": tabs >= 4}
```

The registry keeps part builders behind names so the routine can look each one up and stay deterministic under test.

🟨 **Gotcha — never trust an unregistered part.** A part builder that is not in `PARTS` will raise on lookup; register it first.

<!-- _notes:
(1) KEY CONCEPTS: This slide shows the parts registry the routine depends on: a PARTS dict and a @part decorator that registers each builder by name. sprocket returns a seated base; flange is seated only when it has four tabs. Deterministic returns keep tests stable.

(2) EMPHASIZE: Register by name, look up by name — the clean seam for parts.

(3) REFERENCES:
- [Parts registry (synthetic)](https://example.com/widgets-101/parts) builders and dispatch
-->

---

<!-- _class: dense -->

## Worked Trace — The Routine in Action

A real run of `assemble(sprocket(), flange(tabs=4), gizmo())`:

```text
[Step 1] seat(sprocket)  -> {"part": "sprocket", "seated": true}
[Step 2] flange tabs = 4  (>= MAX_TABS, continue)
[Step 3] clip(flange)    -> {"part": "flange", "tabs": 4, "seated": true}
[Step 4] snap(gizmo)     -> {"part": "gizmo", "seated": true}
         seating_check(widget) -> true
         status = DONE   (widget assembled in 4 steps)
```

🟩 **Production tip:** this trace is exactly what you log per step — part, action, seated. It is the artifact you reach for first when a widget rattles.

<!-- _notes:
(1) KEY CONCEPTS: A real run so you can watch the routine execute. Step 1 seats the sprocket, step 2 confirms four flange tabs, step 3 clips the flange, step 4 snaps the gizmo, and the seating check returns true — status DONE in four steps.

(2) EMPHASIZE: This trace is your logging format. Log part, action, and seated for every step.

(3) REFERENCES:
- [Trace format (synthetic)](https://example.com/widgets-101/trace) per-step logging
-->

---

<!-- _class: dense -->

## The Same Routine in a Jig — What the Fixture Owns

Re-running the routine on a bench jig shows the seam between what you own and what the jig owns:

```python
# widgets_lab/assemble_jig.py — same steps, jig-managed order and holding
class WidgetJig:
    def __init__(self):
        self.parts = []          # jig holds parts in seating order for you

    def add(self, part: dict) -> str:                 # jig enforces the order
        if part["part"] == "gizmo" and not self._has("flange"):
            return "reject"      # jig refuses a gizmo with no seated flange
        self.parts.append(part)
        return "held"
```

| You still own | The jig owns |
|---------------|--------------|
| The parts, the tab count, the seating check | Holding parts in seating order |
| What "done" means for the widget | Rejecting an out-of-order part |

<!-- _notes:
(1) KEY CONCEPTS: This re-runs the same routine on a bench jig to expose the seam. The jig holds parts in seating order and rejects an out-of-order part (a gizmo with no seated flange). You still own the parts, the tab count, the seating check, and what "done" means; the jig owns holding and order enforcement.

(2) EMPHASIZE: The jig owns the plumbing, not the policy. You still decide what "done" means.

(3) REFERENCES:
- [Bench jig (synthetic)](https://example.com/widgets-101/jig) holding and order enforcement
-->

---

<!-- ACT 6: FORMATIVE CHECK #2 — Slide 20 (Apply level) -->

<!-- _class: dense -->

## Diagnose This Assembly — Apply Your Mental Model

**Formative Check 2 of 2 | Level: Apply | ~10 minutes**

An assembly has run but the widget keeps rattling. The last four bench actions are:

```text
snap(gizmo)
seating_check -> RATTLES
snap(gizmo)
seating_check -> RATTLES
```

**What is happening, which check catches it, and what should the bench do when that check fails?**

**Type your diagnosis below.** The platform grades your explanation against a 3-part rubric (root cause, mechanism, fix).

> Open Check 2 (Apply) — natural-language diagnosis · up to 3 attempts · ~10 min
> Graded against root-cause, mechanism, and fix dimensions.

<!-- _notes:
(1) KEY CONCEPTS: This is the Apply-level check. The gizmo is being re-snapped onto a flange that never seated its four tabs, so the seating check keeps returning RATTLES. Root cause: the flange under the gizmo is not fully clipped. Mechanism: the seating check catches the half-clipped flange. Fix: stop re-snapping, go back and clip all four flange tabs, then re-check.

(2) EMPHASIZE: The correct diagnosis names all three: root cause (half-clipped flange), mechanism (seating check fails), and fix (re-clip four tabs, do not re-snap the gizmo).

(3) REFERENCES:
- [Apply-level check (synthetic)](https://example.com/widgets-101/check-2) diagnosis rubric
-->

---

<!-- ACT 7: PRACTICE BRIEF — Slides 21–24 -->

<!-- _class: dense -->

## Lab Brief — Your Turn (WP-1)

**Assemble a widget from scratch, then re-run it on the bench jig.**

- **Raw assembly:** implement seat → clip → snap; confirm the seated log is captured and the flange-tab check and gizmo-snap check both work.
- **Order guard:** reject any gizmo snapped before a seated flange.
- **Deluxe-cap measurement:** record the weight and cost delta of the deluxe gizmo versus the standard cap.
- **Jig re-run:** rebuild the assembly on the `WidgetJig`; produce the same widget, then write 3 notes comparing the two.

**"Done" looks like:** the raw routine returns `DONE` on the happy path; the order guard rejects the out-of-order gizmo; the deluxe-cap table is complete.

🟩 **Submit your lab — auto-checked in ~30s** *(primary)*. The checker scores against the rubric and returns step-level feedback.
*Bench-track:* push your solution to your fork under `submissions/<your-handle>/w1-1/` and open a PR against [example/widgets-101](https://example.com/widgets-101).

<!-- _notes:
(1) KEY CONCEPTS: Here is the hands-on work — WP-1, the module project. Four parts: raw assembly (seat, clip, snap with the checks), an order guard (reject a gizmo before a seated flange), a deluxe-cap measurement (weight and cost delta), and a jig re-run with three comparison notes. "Done" is concrete: DONE on the happy path, the order guard fires, the deluxe-cap table is complete.

(2) EMPHASIZE: Each part is a skill you can name — build them reusable; the raw routine carries into W1.2.

(3) REFERENCES:
- [WP-1 spec (synthetic)](https://example.com/widgets-101/wp-1) the module project
-->

---

<!-- _class: dense -->

## Assessment Rubric

| Criterion | Proficient | Exemplary |
|-----------|-----------|-----------|
| **Assembly correctness** | Routine runs end-to-end; appends seated parts | Handles a half-clipped flange and an unknown part gracefully |
| **Seating check** | Sprocket, flange, gizmo checks work | Rattle test and weight check added and passing |
| **Deluxe-cap measurement** | Weight/cost table populated | Names when the deluxe cap is worth its extra weight |
| **Order guard** | Rejects a gizmo before a seated flange | Names where an out-of-order part would slip through and adds a guard |
| **Jig comparison** | Jig version matches the raw widget | 3 notes correctly attribute what the jig owns vs. hides |

🟩 **Production tip:** your raw `assemble.py` is reusable across the milestone — write seat-and-clip cleanly and W1.2's finishing layer slots straight on top.

**[Submit for auto-checked scoring](https://example.com/widgets-101/submit)** — the checker scores each criterion above. Bench-track: PR against [example/widgets-101](https://example.com/widgets-101) under `submissions/<your-handle>/w1-1/`.

<!-- _notes:
(1) KEY CONCEPTS: The rubric distinguishes Proficient from Exemplary on each criterion. Assembly correctness: Proficient runs end-to-end; Exemplary handles a half-clipped flange and unknown parts. Seating check: Proficient runs the three checks; Exemplary adds the rattle and weight checks. And so on across the deluxe-cap measurement, the order guard, and the jig comparison.

(2) EMPHASIZE: Exemplary is defined by handling failure and interpreting results, not just the happy path.

(3) REFERENCES:
- [Self-assessment rubric (synthetic)](https://example.com/widgets-101/rubric) companion self-scoring
-->

---

## Time, Support, and Resources

**Expected time to complete WP-1:** 3–4 (fictional) hours (raw assembly + order guard + deluxe-cap measurement + jig re-run).

**Scaffold code:** the course repo provides the `parts.py` registry and the test stubs.

**Setup check:** before starting, run the provided part unit tests — if `sprocket` and `flange` pass, your bench is ready for the assembly work.

**Where to get help:**

- **Bench tutor** (live, in-page) — a synthetic inline assistant; ask about the routine, the trace, or your own code.
- **Report an issue / ask the cohort** — the support widget files a pre-tagged issue (auto-labels `module:w1-1`).
- **Reflection page** — [Open on example.com](https://example.com/widgets-101/reflection) ([offline mirror](https://example.com/widgets-101/reflection)).

**Catch-up path:** start with the sprocket and flange only. Get a clean two-part seat, then add the gizmo and the guard.

<!-- _notes:
(1) KEY CONCEPTS: Three to four fictional hours covers the raw assembly, the order guard, the deluxe-cap measurement, and the jig re-run. The scaffold provides the parts registry and test stubs. The setup check is a binary gate: if the sprocket and flange part tests pass, the bench is ready. Help channels: a live bench tutor, a support widget that files a pre-tagged issue, and a reflection page with an offline mirror.

(2) EMPHASIZE: The catch-up path is the debugging strategy — two parts seating cleanly first, then add the gizmo.

(3) REFERENCES:
- [WP-1 setup (synthetic)](https://example.com/widgets-101/setup) scaffold and test stubs
-->

---

## Reflection Prompt

Take **5 minutes** before you start WP-1. Write your answers in a text file.

**1. Where did your assembly fail to reach `DONE`?**
> Was it a half-clipped flange, a gizmo snapped too early, or a missed seating check? What does that reveal about how you ordered the steps?

**2. Which fictional parts are genuinely required vs. bolt-on?**
> Give one concrete example of each — a part the spec calls for, and a part that only adds weight.

**3. What is your biggest open question about testing the seating check?**
> Write it down now; return to it after the lab and note whether assembling the widget resolved it.

**Full reflection page:** [Open on example.com](https://example.com/widgets-101/reflection) — synthetic rendering with facilitator notes. ([Offline mirror](https://example.com/widgets-101/reflection))
**Self-assessment rubric:** [Open on example.com](https://example.com/widgets-101/rubric) — five-criterion self-scoring (threshold 8/15). ([Offline mirror](https://example.com/widgets-101/rubric))

<!-- _notes:
(1) KEY CONCEPTS: Three reflection questions close the module — write actual answers before WP-1. Question one is diagnosis: where did assembly fail to reach DONE, and what does that reveal about your step order? Question two is the transfer question from Concept 5: which parts are genuinely required versus bolt-on? Question three invites an open question about testing the seating check.

(2) EMPHASIZE: Question two is the one that turns the module actionable — name a required part and a bolt-on part.

(3) REFERENCES:
- [Reflection prompts (synthetic)](https://example.com/widgets-101/reflection) facilitator notes
-->

---

## What You Have Built + What Is Next

**This module produces:**

- A raw assembly routine with a flange-tab check, a gizmo-snap check, and a seating check
- An order guard and a deluxe-cap measurement with a weight/cost trade-off
- A jig re-run and three written notes on jig trade-offs

**Next module: W1.2 — Finishing the Widget**
W1.1 assembled the widget. It is still bare metal — it does not yet have paint or polish. W1.2 adds a finishing pass so the widget survives inspection.

🟪 **Placeholder signal:** the raw assembly routine is the strongest (make-believe) evidence for skill-100 — it is the artifact you point to when someone asks you to "walk me through assembling a widget."

*Data source: synthetic widget-maker skills matrix (skill-100, score 3, High demand). Entirely fictional.*

<!-- _notes:
(1) KEY CONCEPTS: The closing slide accounts for what you built and points to what is next. Three artifacts: a raw assembly routine with three checks; an order guard plus a deluxe-cap measurement; and a jig re-run with three notes. Then the bridge to W1.2: the widget is assembled but bare metal, and finishing adds paint and polish before inspection.

(2) EMPHASIZE: The raw routine is your skill-100 answer — walk someone through the four steps you wrote by hand.

(3) REFERENCES:
- [W1.2 — finishing a widget](https://example.com/widgets-101/w1-2) the next module
- [Synthetic skills matrix](https://example.com/widgets-101/skills) skill-100 = 3
-->
